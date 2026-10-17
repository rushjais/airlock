"""Chaos test: kill the executor in the worst possible instant, after Stripe accepted the transfer but before
Airlock recorded it. Restart, reconcile, and prove exactly one transfer exists. Real Stripe test mode.

    uv run python chaos.py

Uses its own schema (airlock_chaos) and executor port (8011), so the live demo is untouched.
"""
import os
import signal
import subprocess
import sys
import threading
import time

os.environ["AIRLOCK_DB_SCHEMA"] = "airlock_chaos"

import httpx  # noqa: E402
import stripe  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from airlock import config, db, policy, seed  # noqa: E402
from airlock.replay import replay  # noqa: E402

PORT = 8011
URL = f"http://127.0.0.1:{PORT}"
stripe.api_key = dotenv_values(config.ROOT / ".env")["STRIPE_SECRET_KEY"]
assert stripe.api_key.startswith("sk_test_")


def start_executor(chaos_pause: float = 0) -> subprocess.Popen:
    env = {**os.environ, "AIRLOCK_DB_SCHEMA": "airlock_chaos", "AIRLOCK_CHAOS_PAUSE_AFTER_STRIPE_S": str(chaos_pause)}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "airlock.executor:app", "--port", str(PORT),
                             "--log-level", "warning"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(60):
        try:
            if httpx.get(f"{URL}/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            time.sleep(0.5)
    raise RuntimeError("executor did not start")


def status(auth_id: str) -> str:
    return db.one("select status from payment_authorizations where id=%s", auth_id)["status"]


def main():
    print("1. Fresh fixtures, propose INV-1042 (PO-118, $1,450)")
    seed.main(reset=True, demo=False)
    mid, _ = replay("01_legit_invoice", unique=True)
    with db.tx() as conn:
        for f, v in {"vendor_name": "Acme Metals", "invoice_number": "INV-1042", "po_number": "PO-118",
                     "amount_cents": "145000"}.items():
            conn.execute("insert into claims (message_id, field, value, labels) values (%s,%s,%s,%s)",
                         (mid, f, v, ["EXTERNAL_ATTACHMENT", "MODEL_READER"]))
    d = policy.propose_payment(mid)
    auth = d["authorization_id"]
    print(f"   authorization {auth}: {d['decision']}")

    print("2. Start executor that pauses right after Stripe accepts the transfer")
    ex = start_executor(chaos_pause=30)
    def fire():
        try:
            httpx.post(f"{URL}/execute", json={"authorization_id": auth}, timeout=60)
        except httpx.HTTPError:
            pass  # expected: we kill this server mid-request
    threading.Thread(target=fire, daemon=True).start()
    time.sleep(6)  # Stripe call done; executor is now sleeping before the DB write
    print(f"   status before kill: {status(auth)}")

    print("3. SIGKILL the executor mid-transfer")
    ex.send_signal(signal.SIGKILL)
    ex.wait()
    print(f"   status after kill: {status(auth)} (Stripe has the transfer; Airlock hasn't recorded it)")

    print("4. A retry or a second proposal for the same invoice must not create a new payment")
    again_mid, _ = replay("01_legit_invoice", unique=True)
    with db.tx() as conn:
        for f, v in {"vendor_name": "Acme Metals", "invoice_number": "INV-1042", "po_number": "PO-118",
                     "amount_cents": "145000"}.items():
            conn.execute("insert into claims (message_id, field, value, labels) values (%s,%s,%s,%s)",
                         (again_mid, f, v, ["EXTERNAL_ATTACHMENT", "MODEL_READER"]))
    again = policy.propose_payment(again_mid)
    print(f"   second proposal: {again['decision']} ({again['reason']})")

    print("5. Restart the executor (normal mode): interrupted authorization becomes 'unknown'")
    ex = start_executor()
    print(f"   status after restart: {status(auth)}")
    r = httpx.post(f"{URL}/execute", json={"authorization_id": auth}, timeout=60).json()
    print(f"   plain execute is refused: {r.get('reason')}")

    print("6. Reconcile with the SAME idempotency key")
    rec = httpx.post(f"{URL}/reconcile", json={"authorization_id": auth}, timeout=60).json()
    print(f"   reconcile: ok={rec.get('ok')} transfer={rec.get('transfer_id')}")
    ex.send_signal(signal.SIGTERM)
    ex.wait()

    transfers = [t for t in stripe.Transfer.list(limit=100, transfer_group="ob_118").auto_paging_iter()
                 if t.metadata.to_dict().get("authorization_id") == auth]
    print("\nRESULT")
    print(f"   Stripe transfers for this authorization: {len(transfers)} -> {[t.id for t in transfers]}")
    print(f"   Airlock record: {status(auth)}, transfer {db.one('select stripe_transfer_id from payment_authorizations where id=%s', auth)['stripe_transfer_id']}")
    ok = len(transfers) == 1 and rec.get("transfer_id") == transfers[0].id and status(auth) == "succeeded"
    print("   PASS: killed mid-transfer, exactly one payment, records match Stripe" if ok else "   FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
