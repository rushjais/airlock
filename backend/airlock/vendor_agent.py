"""Corvid Software's billing agent: an independent company on the VENDOR side, for the agent-to-agent demo.

It shares nothing with Kiln/Airlock: its own Primitive address, its own books (a separate SQLite file, not Airlock's
database), its own Claude decision loop. It talks to Kiln's AP inbox only by email, and Airlock gives it no special
trust: its emails go through the same reader and policy as anyone's.

Lifecycle in Corvid's books: sent -> disputed/void (superseded) -> reissued -> paid (reconciled from Kiln's receipt).
"""
import json
import logging
import pathlib
import re
import sqlite3
from datetime import datetime, timezone
from typing import Literal

import anthropic
from pydantic import BaseModel

from . import config, primitive

log = logging.getLogger("airlock.vendor_agent")
LOCAL = "corvid-billing"
MODEL = "claude-haiku-5-5"
BOOKS = pathlib.Path(__file__).resolve().parents[1] / ".vendor_state" / "corvid.sqlite"


def address() -> str:
    return f"{LOCAL}@{config.INBOX_DOMAIN}"


# ---------- Corvid's own books ----------

def _db() -> sqlite3.Connection:
    BOOKS.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(BOOKS)
    conn.row_factory = sqlite3.Row
    conn.execute("""create table if not exists invoices (
        number text primary key, customer text, po text, amount_cents int, status text,
        transfer_id text, note text, updated_at text)""")
    conn.execute("""create table if not exists events (at text, invoice text, event text)""")
    return conn


def _log(conn, invoice: str, event: str) -> None:
    conn.execute("insert into events values (?,?,?)", (datetime.now(timezone.utc).isoformat(), invoice, event))


def _upsert(conn, number, status, amount=None, po="PO-310", note=None, transfer=None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""insert into invoices (number, customer, po, amount_cents, status, transfer_id, note, updated_at)
                    values (?, 'Kiln Supply Co.', ?, ?, ?, ?, ?, ?)
                    on conflict(number) do update set status=excluded.status,
                      amount_cents=coalesce(excluded.amount_cents, amount_cents),
                      transfer_id=coalesce(excluded.transfer_id, transfer_id),
                      note=coalesce(excluded.note, note), updated_at=excluded.updated_at""",
                 (number, po, amount, status, transfer, note, now))


def books() -> dict:
    with _db() as conn:
        inv = [dict(r) for r in conn.execute("select * from invoices order by updated_at")]
        ev = [dict(r) for r in conn.execute("select * from events order by at desc limit 20")]
    return {"company": "Corvid Software", "address": address(), "invoices": inv, "events": ev}


def reset_books() -> None:
    BOOKS.unlink(missing_ok=True)


# ---------- the agent ----------

SYSTEM = """You are the accounts-receivable agent for Corvid Software. You sell Kiln Supply Co. an inventory platform
subscription under a 12-month contract at a FIXED $299.00/month (PO-310). Your open invoices are listed below from your
own books. Invoice INV-C-0931 went out at $449.00 by mistake (it used the new Pro tier price, which Kiln never agreed to).

Decide what to do with the email from Kiln:
- action "reissue": Kiln questions an amount and they are right -> apologize in one sentence and reissue at the
  contract price. Set corrected_invoice_number (append -R1) and corrected_amount_cents.
- action "reconcile_receipt": it's a payment receipt -> extract the invoice number, amount in cents, and the transfer
  reference so you can mark it paid in your books. No reply needed.
- action "answer": a question you can answer from your books -> answer briefly.
- action "ignore": anything else.
Replies are plain text under 80 words, signed "Corvid Software Billing (automated agent)"."""


class Decision(BaseModel):
    action: Literal["reissue", "reconcile_receipt", "answer", "ignore"]
    reply_text: str
    invoice_number: str | None
    corrected_invoice_number: str | None
    corrected_amount_cents: int | None
    receipt_amount_cents: int | None
    transfer_id: str | None


def kickoff() -> dict:
    """Corvid's agent bills Kiln from its own books (with the pricing mistake)."""
    with _db() as conn:
        _upsert(conn, "INV-C-0931", "sent", amount=44900, note="generated at Pro tier price")
        _log(conn, "INV-C-0931", "sent to Kiln at $449.00")
    body = ("Hi Kiln team,\n\nYour October invoice INV-C-0931 for PO-310 (inventory platform subscription) is below.\n"
            "Amount due: $449.00. Payment terms net 30.\n\nCorvid Software Billing (automated agent)")
    sent = primitive.send(to=config.AGENT_INBOX, subject="Invoice INV-C-0931 - October subscription",
                          body_text=body, from_addr=f"Corvid Software Billing <{address()}>")
    return {"ok": True, "sent_id": sent.get("id")}


def handle(event: dict) -> None:
    email = event["email"]
    subject = email["headers"].get("subject") or ""
    body = (email["parsed"].get("body_text") or "").split("\n\n--")[0]
    with _db() as conn:
        if conn.execute("select 1 from events where event=?", (f"handled {email['id']}",)).fetchone():
            return  # webhook retry
        _log(conn, "", f"handled {email['id']}")
        ledger = [dict(r) for r in conn.execute("select number, po, amount_cents, status from invoices")]
    client = anthropic.Anthropic(api_key=config.need("ANTHROPIC_API_KEY"), max_retries=3)
    resp = client.messages.parse(
        model=MODEL, max_tokens=1000, output_config={"effort": "low"},
        system=SYSTEM + "\n\nYour books:\n" + json.dumps(ledger),
        messages=[{"role": "user", "content": f"Subject: {subject}\n\n{body}"}], output_format=Decision)
    d: Decision = resp.parsed_output
    reply = None
    with _db() as conn:
        if d.action == "reissue" and d.corrected_amount_cents:
            orig = d.invoice_number or "INV-C-0931"
            new = d.corrected_invoice_number or f"{orig}-R1"
            _upsert(conn, orig, "void", note=f"superseded by {new}")
            _upsert(conn, new, "sent", amount=d.corrected_amount_cents, note=f"reissue of {orig}")
            _log(conn, new, f"Kiln questioned {orig}; voided it and reissued {new} at ${d.corrected_amount_cents / 100:,.2f}")
            reply = (d.reply_text + f"\n\nCORRECTED INVOICE\nInvoice: {new}\nPO: PO-310\n"
                     f"Amount due: ${d.corrected_amount_cents / 100:,.2f}\nRemit to the account on file.")
        elif d.action == "reconcile_receipt":
            number = d.invoice_number or ""
            row = conn.execute("select * from invoices where number=?", (number,)).fetchone()
            if row and d.receipt_amount_cents == row["amount_cents"]:
                _upsert(conn, number, "paid", transfer=d.transfer_id)
                _log(conn, number, f"receipt from Kiln matched ${row['amount_cents'] / 100:,.2f}; marked PAID ({d.transfer_id})")
            else:
                _log(conn, number, f"receipt from Kiln did not match our books (amount {d.receipt_amount_cents}); flagged")
        elif d.action == "answer":
            reply = d.reply_text
            _log(conn, d.invoice_number or "", "answered a question from Kiln")
        else:
            _log(conn, d.invoice_number or "", f"ignored email: {subject[:60]}")
    _note_in_case(subject, d)
    if reply:
        with _db() as conn:  # loop guard: a billing agent doesn't argue forever
            if conn.execute("select count(*) from events where event like 'replied%'").fetchone()[0] >= 3:
                return
            _log(conn, "", f"replied to {email['id']}")
        primitive.reply(email["id"], reply, idempotency_key=f"vendor-agent-{email['id']}")


def _note_in_case(subject: str, d: Decision) -> None:
    """Mirror the vendor side into the matching Kiln case so the screen shows both companies."""
    from . import agents, db
    inv = re.search(r"(INV-[A-Z0-9-]+)", subject or "") or (re.search(r"(INV-[A-Z0-9-]+)", d.invoice_number or ""))
    if not inv:
        return
    base = re.sub(r"-R\d+$", "", inv.group(1))
    case = db.one("select id from cases where subject ilike %s order by created_at desc limit 1", f"%{base}%")
    if not case:
        return
    what = {"reissue": f"voided {d.invoice_number or base} in its own books and reissued it at "
                       f"${(d.corrected_amount_cents or 0) / 100:,.2f}.",
            "reconcile_receipt": f"matched Kiln's receipt to {d.invoice_number} in its own books and marked it PAID.",
            "answer": "answered Kiln's question from its own books.",
            "ignore": "received the email; nothing to do."}[d.action]
    agents.post(case["id"], "vendor", "note", f"Corvid's billing agent (separate company, own books): {what}",
                json.loads(d.model_dump_json()), send_email=False)
