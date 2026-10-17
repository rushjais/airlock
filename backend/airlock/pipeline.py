"""What happens to one inbound email, end to end.

ingest (already stored) -> reader -> policy -> [executor] -> templated in-thread reply.
This module never sees the Stripe key; payments go through the executor process by authorization_id only.
"""
import logging

import httpx

from . import config, db, policy, primitive, reader
from .templates import render

log = logging.getLogger("airlock.pipeline")


def execute_payment(authorization_id: str) -> dict:
    r = httpx.post(f"{config.EXECUTOR_URL}/execute", json={"authorization_id": authorization_id}, timeout=60)
    return r.json()


def _reply(message_id: str, template: str, **values) -> None:
    msg = db.one("select provider_message_id from messages where id=%s", message_id)
    body = render(template, **values)
    provider_id = None
    if not msg["provider_message_id"].startswith("replay:"):
        try:
            sent = primitive.reply(msg["provider_message_id"], body, idempotency_key=f"airlock-{message_id}-{template}")
            provider_id = sent.get("id")
        except httpx.HTTPError as e:
            log.warning("reply failed: %s", e)
    to = db.one("select from_address from messages where id=%s", message_id)["from_address"]
    db.one("""insert into outbound_messages (channel, to_address, template, in_reply_to_message_id, provider_id, body)
              values ('email',%s,%s,%s,%s,%s) returning id""", to, template, message_id, provider_id, body)


def process(message_id: str) -> dict:
    reader.read(message_id)
    decision = policy.propose_payment(message_id)
    facts = decision["facts"]
    vendor = db.one("select * from vendors where id=%s", facts.get("vendor_id")) if facts.get("vendor_id") else None
    common = dict(vendor=vendor["name"] if vendor else "there", invoice=facts.get("invoice_number") or "your invoice",
                  po=facts.get("po_number") or "this charge", last4=vendor["destination_last4"] if vendor else "")

    if decision["next_action"] == "execute_payment":
        result = execute_payment(decision["authorization_id"])
        decision["execution"] = result
        if result.get("ok"):
            _reply(message_id, "receipt", amount=result["amount_cents"], transfer_id=result["transfer_id"], **common)
    elif decision["next_action"] == "reply_in_thread:already_paid":
        _reply(message_id, "already_paid", **common)
    elif decision["next_action"] == "reply_in_thread:ask_vendor_to_explain_or_reissue":
        _reply(message_id, "ask_vendor_to_explain_or_reissue", claimed=facts.get("claimed_cents"),
               expected=facts.get("obligation_cents"), **common)
    elif decision["next_action"] == "request_vendor_verification":
        _reply(message_id, "verification_pending", **common)
    log.info("message %s -> %s (%s)", message_id, decision["decision"], decision["reason"])
    return decision
