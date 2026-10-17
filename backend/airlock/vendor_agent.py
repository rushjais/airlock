"""Corvid Software's billing agent: a separate agent on the VENDOR's side, for the agent-to-agent dispute demo.

It lives at its own Primitive address and talks to Kiln's AP inbox only by email, like any outside party.
Airlock gives it no special trust: its emails go through the same reader and policy as everyone else's.

Flow: it sends an invoice at $449 (contract is $299) -> Airlock holds it and asks to explain or reissue ->
this agent replies in the thread with a corrected $299 invoice -> Airlock pays it and sends a receipt.
"""
import json
import logging
import re

import anthropic
from pydantic import BaseModel

from . import config, db, primitive

log = logging.getLogger("airlock.vendor_agent")
LOCAL = "corvid-billing"
MODEL = "claude-haiku-5-5"


def address() -> str:
    return f"{LOCAL}@{config.INBOX_DOMAIN}"


SYSTEM = """You are the accounts-receivable agent for Corvid Software, which sells Kiln Supply Co. an inventory
platform subscription under a 12-month contract at a FIXED $299.00/month (PO-310). This month's invoice INV-C-0931 was
generated at $449.00 by mistake: it used the new Pro tier price, which Kiln never agreed to.
You will receive an email from Kiln's accounts-payable team. If they question the amount, apologize briefly, explain
the cause in one sentence, and reissue the invoice at the contract price. If the email is just a payment receipt or a
thank-you, do not reply. Keep replies under 80 words, plain text, signed "Corvid Software Billing (automated agent)"."""


class VendorReply(BaseModel):
    should_reply: bool
    reply_text: str
    corrected_invoice_number: str | None
    corrected_amount_cents: int | None


def kickoff() -> dict:
    """Corvid's agent sends the overpriced invoice to Kiln's AP inbox."""
    body = ("Hi Kiln team,\n\nYour October invoice INV-C-0931 for PO-310 (inventory platform subscription) is below.\n"
            "Amount due: $449.00. Payment terms net 30.\n\nCorvid Software Billing (automated agent)")
    sent = primitive.send(to=config.AGENT_INBOX, subject="Invoice INV-C-0931 - October subscription",
                          body_text=body, from_addr=f"Corvid Software Billing <{address()}>",
                          idempotency_key=None)
    return {"ok": True, "sent_id": sent.get("id")}


def handle(event: dict) -> None:
    """An email arrived for Corvid's agent (e.g. Kiln's question about the amount). Reply at most once per thread."""
    email = event["email"]
    subject = email["headers"].get("subject") or ""
    body = (email["parsed"].get("body_text") or "").split("\n\n--")[0]
    thread = re.sub(r"^(re:\s*)+", "", subject, flags=re.I).strip().lower()
    if db.one("select 1 from outbound_messages where template='vendor_agent_reply' and to_address=%s", thread):
        log.info("vendor agent already answered thread %r; staying quiet", thread)
        return
    client = anthropic.Anthropic(api_key=config.need("ANTHROPIC_API_KEY"), max_retries=3)
    resp = client.messages.parse(model=MODEL, max_tokens=1000, system=SYSTEM, output_config={"effort": "low"},
                                 messages=[{"role": "user", "content": f"Subject: {subject}\n\n{body}"}],
                                 output_format=VendorReply)
    r: VendorReply = resp.parsed_output
    _note_in_case(subject, r)
    if not r.should_reply:
        return
    text = r.reply_text
    if r.corrected_amount_cents:
        inv = r.corrected_invoice_number or "INV-C-0931-R1"
        text += (f"\n\nCORRECTED INVOICE\nInvoice: {inv}\nPO: PO-310\nAmount due: ${r.corrected_amount_cents / 100:,.2f}\n"
                 "Remit to the account on file.")
    primitive.reply(email["id"], text, idempotency_key=f"vendor-agent-{email['id']}")
    db.one("""insert into outbound_messages (channel, to_address, template, body) values ('email',%s,'vendor_agent_reply',%s)
              returning id""", thread, text)


def _note_in_case(subject: str, r: VendorReply) -> None:
    """Show the vendor side in the original case thread, clearly labeled as the vendor's agent."""
    from . import agents
    inv = re.search(r"(INV-[A-Z0-9-]+)", subject or "")
    if not inv:
        return
    case = db.one("select id from cases where subject ilike %s order by created_at limit 1", f"%{inv.group(1)}%")
    if case:
        what = (f"Received Kiln's question and is reissuing at ${r.corrected_amount_cents / 100:,.2f}."
                if r.corrected_amount_cents else "Received Kiln's email; no reply needed.")
        agents.post(case["id"], "vendor", "note", f"Corvid's billing agent (vendor side, by email): {what}",
                    json.loads(r.model_dump_json()), send_email=False)
