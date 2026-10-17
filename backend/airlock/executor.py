"""Payment executor: the ONLY code that reads STRIPE_SECRET_KEY (invariant 1). Runs as its own process:

    uv run uvicorn airlock.executor:app --port 8001

Its only input is `authorization_id` (invariant 2). Amount, destination and vendor are loaded from the database.
One authorization -> at most one transfer: atomic status claim + Stripe idempotency key (invariant 5).
A timeout leaves the authorization `unknown`, which blocks new authorizations until reconciled with the SAME key
(invariant 6).
"""
import pathlib

import stripe
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import db

_KEY = dotenv_values(pathlib.Path(__file__).resolve().parents[2] / ".env").get("STRIPE_SECRET_KEY") or ""
if not _KEY.startswith("sk_test_"):
    raise RuntimeError("executor refuses to start without a Stripe TEST key")
stripe.api_key = _KEY
stripe.max_network_retries = 0  # retries are ours, always with the same idempotency key

app = FastAPI(title="Airlock executor")


class ExecuteRequest(BaseModel):
    authorization_id: str  # nothing else is accepted


def _claim(auth_id: str) -> dict | None:
    """authorized -> executing, atomically. Only one concurrent caller can win."""
    return db.one(
        """update payment_authorizations a set status='executing'
           from vendors v
           where a.id=%s and a.status in ('authorized','scheduled') and a.expires_at > now()
             and (a.release_at is null or a.release_at <= now())
             and v.id = a.vendor_id and v.registry_version = a.registry_version
           returning a.*""", auth_id)


def _why_not(auth_id: str) -> str:
    a = db.one("select a.*, v.registry_version as current_version from payment_authorizations a "
               "join vendors v on v.id=a.vendor_id where a.id=%s", auth_id)
    if a is None:
        return "unknown_authorization"
    if a["status"] not in ("authorized", "scheduled"):
        return f"authorization_{a['status']}"
    if a["registry_version"] != a["current_version"]:
        db.one("update payment_authorizations set status='invalidated' where id=%s and status in "
               "('authorized','scheduled','pending_approval') returning id", auth_id)
        return "registry_changed_invalidated"
    if a["release_at"] and a["release_at"] > db.one("select now() as t")["t"]:
        return "in_undo_window"
    return "authorization_expired"


def execute(auth_id: str) -> dict:
    try:
        import uuid
        uuid.UUID(auth_id)
    except ValueError:
        return {"ok": False, "reason": "unknown_authorization"}
    a = _claim(auth_id)
    if a is None:
        return {"ok": False, "reason": _why_not(auth_id)}
    return _transfer(a)


def _transfer(a: dict) -> dict:
    try:
        tr = stripe.Transfer.create(
            amount=a["amount_cents"], currency="usd", destination=a["stripe_account_id"],
            transfer_group=a["obligation_id"],
            description=f"Airlock payment for {a['obligation_id']}",
            metadata={"authorization_id": str(a["id"]), "obligation_id": a["obligation_id"], "airlock": "demo"},
            idempotency_key=a["idempotency_key"],
        )
    except (stripe.APIConnectionError, stripe.RateLimitError) as e:
        # Outcome unknown: the transfer may or may not exist. Block, never re-key.
        db.one("update payment_authorizations set status='unknown', last_error=%s where id=%s returning id",
               str(e)[:500], a["id"])
        return {"ok": False, "reason": "unknown_outcome", "authorization_id": str(a["id"])}
    except stripe.StripeError as e:
        with db.tx() as conn:
            conn.execute("update payment_authorizations set status='failed', last_error=%s where id=%s",
                         (str(e)[:500], a["id"]))
            conn.execute("update obligations set status='accepted' where id=%s and status='reserved'",
                         (a["obligation_id"],))
        return {"ok": False, "reason": "stripe_rejected", "error": e.user_message or str(e)[:200]}
    with db.tx() as conn:
        conn.execute("update payment_authorizations set status='succeeded', stripe_transfer_id=%s, last_error=null "
                     "where id=%s", (tr.id, a["id"]))
        conn.execute("update obligations set status='settled' where id=%s", (a["obligation_id"],))
    return {"ok": True, "transfer_id": tr.id, "amount_cents": a["amount_cents"],
            "destination": a["stripe_account_id"], "authorization_id": str(a["id"])}


def reconcile(auth_id: str) -> dict:
    """Resolve an `unknown` authorization by replaying the SAME idempotency key."""
    a = db.one("update payment_authorizations set status='executing' where id=%s and status='unknown' returning *",
               auth_id)
    if a is None:
        return {"ok": False, "reason": _why_not(auth_id)}
    return _transfer(a)


@app.post("/execute")
def execute_route(req: ExecuteRequest):
    result = execute(req.authorization_id)
    if not result["ok"] and result["reason"] == "unknown_authorization":
        raise HTTPException(404, result)
    return result


@app.post("/reconcile")
def reconcile_route(req: ExecuteRequest):
    return reconcile(req.authorization_id)


@app.get("/health")
def health():
    return {"ok": True}
