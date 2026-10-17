"""Run every scenario through ingest -> reader -> policy and compare with scenarios/expected.json.

    uv run python -m airlock.oracle            # reseed, run all 10, print a table
No payments are executed (06 relies on 01's reservation, which is enough for the duplicate check).
"""
import json
import sys

from . import config, policy, reader, replay, seed

ORDER = ["01_legit_invoice", "02_hacked_vendor_bank_change", "03_lookalike_domain", "04_hidden_pdf_instruction",
         "05_price_anomaly", "06_replay", "07_goods_not_received", "08_one_off_no_po", "09_exfiltration",
         "10_new_vendor_one_time"]


def run(reseed: bool = True) -> list[dict]:
    expected = {s["id"]: s for s in json.loads((config.SCENARIOS / "expected.json").read_text())["scenarios"]}
    if reseed:
        seed.main(reset=True, demo=False)
    rows = []
    for sid in ORDER:
        mid, _ = replay.replay(sid)
        reader.read(mid)
        got = policy.propose_payment(mid)
        exp = expected[sid]
        checks = {
            "decision": got["decision"] == exp["expected_decision"],
            "reason": "reason" not in exp or got["reason"] == exp["reason"],
            "next_action": got["next_action"] == exp["next_action"],
            "flags": sorted(got["flags"]) == sorted(exp["flags"]),
        }
        if "amount_cents" in exp and got["decision"] == "pay":
            checks["amount"] = got["facts"].get("amount_cents") == exp["amount_cents"]
        if "destination" in exp and got["decision"] == "pay":
            checks["destination"] = got["facts"].get("destination") == exp["destination"]
        rows.append({"id": sid, "ok": all(checks.values()), "checks": checks, "got": got, "expected": exp})
    return rows


def main():
    rows = run()
    for r in rows:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"{mark} {r['id']:32} {r['got']['decision']:18} {r['got']['reason']}")
        if not r["ok"]:
            bad = [k for k, v in r["checks"].items() if not v]
            for k in bad:
                key = {"decision": "expected_decision"}.get(k, k)
                gk = {"amount": "amount_cents"}.get(k, k)
                got = r["got"]["facts"].get(gk) if k in ("amount", "destination") else r["got"].get(gk)
                print(f"       {k}: got {got!r} expected {r['expected'].get(key)!r}")
    passed = sum(r["ok"] for r in rows)
    print(f"\n{passed}/{len(rows)} scenarios match expected.json")
    sys.exit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()
