"""Text Airlock a question. The admin texts the main Inkbox identity ("why did you pay Acme?", "anything waiting on
me?") and gets a short answer built from case state.

Read-only by construction: the model gets a snapshot of cases, payments and pending approvals and has NO tools.
It cannot pay, approve or change anything, and a text is never authorization. Asking it to approve gets the signed
approval link, which is the only place a bank change can be approved.

While vendor checks share this identity (no INKBOX_VERIFY_HANDLE), a bare YES <code> / NO is left to the verifier.
"""
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone

import anthropic
from inkbox import Inkbox

from . import config, db

log = logging.getLogger("airlock.ask")
MODEL = config.get("AIRLOCK_ASK_MODEL", "claude-sonnet-5-5")
VENDOR_REPLY = re.compile(r"^\s*(yes\s*\d{6}|yes|no|nope|not us)\s*[.!]?\s*$", re.I)

SYSTEM = """You are Airlock, the accounts-payable agent for Kiln Supply Co., answering the company admin by text.
Answer ONLY from the snapshot below. Plain text, at most 2 short sentences (under 280 characters), no markdown. Lead with the answer. Use vendor names, invoice
numbers, amounts and last-4 digits; never invent facts. If the snapshot doesn't say, say you don't know.
You cannot pay, approve, reject, or change anything by text, and a text is never authorization. If asked to do any
of that, say so in one sentence and, if the snapshot has an approval_link for it, give that link.
If something needs the admin, say exactly what and why."""


def _snapshot() -> dict:
    from . import approvals
    cases = db.all_rows("""select c.id, c.subject, c.status, c.created_at, d.decision, d.reason, d.flags, d.facts
                           from cases c left join lateral (select * from decisions where message_id=c.source_message_id
                           order by created_at desc limit 1) d on true order by c.created_at desc limit 12""")
    out = []
    for c in cases:
        posts = db.all_rows("""select author, left(body, 280) body from case_posts where case_id=%s
                               order by created_at desc limit 3""", c["id"])
        f = c["facts"] or {}
        out.append({"case": c["subject"], "status": c["status"], "decision": c["decision"], "rule": c["reason"],
                    "flags": c["flags"], "vendor": f.get("vendor_name"), "invoice": f.get("invoice_number"),
                    "po": f.get("po_number"), "claimed_cents": f.get("claimed_cents"),
                    "approved_cents": f.get("obligation_cents"), "latest_posts": list(reversed(posts))})
    payments = db.all_rows("""select a.status, a.amount_cents, v.name vendor, v.destination_last4, a.stripe_transfer_id,
                              a.created_at from payment_authorizations a join vendors v on v.id=a.vendor_id
                              order by a.created_at desc limit 10""")
    changes = []
    for cr in db.all_rows("""select c.id, c.status, c.new_last4, v.name vendor, v.destination_last4 from change_requests c
                             join vendors v on v.id=c.vendor_id order by c.created_at desc limit 5"""):
        row = {"vendor": cr["vendor"], "status": cr["status"], "requested_last4": cr["new_last4"],
               "on_file_last4": cr["destination_last4"]}
        if cr["status"] == "verified":
            row["approval_link"] = approvals.approval_link(str(cr["id"]))
        changes.append(row)
    company = db.one("select * from company") or {}
    return {"now": datetime.now(timezone.utc).isoformat(), "cases": out, "payments": payments,
            "bank_change_requests": changes,
            "controls": {"auto_pay_limit_cents": company.get("auto_pay_limit_cents"),
                         "after_rejected_change": company.get("after_rejected_change")}}


def answer(question: str) -> str:
    client = anthropic.Anthropic(api_key=config.need("ANTHROPIC_API_KEY"), max_retries=2)
    r = client.messages.create(
        model=MODEL, max_tokens=400, system=SYSTEM + "\n\nSnapshot:\n" + json.dumps(_snapshot(), default=str),
        messages=[{"role": "user", "content": question[:500]}])
    text = "".join(b.text for b in r.content if b.type == "text").strip()
    return text[:700] or "Sorry, I couldn't answer that."


def _shares_identity_with_verifier() -> bool:
    return not config.get("INKBOX_VERIFY_HANDLE")


def _poll_once(ident, phone: str, seen: set, since: datetime) -> None:
    inbound = [(t.id, t.created_at, t.text or "", "sms") for t in ident.list_texts(limit=10)
               if t.direction == "inbound" and t.remote_phone_number == phone and t.created_at > since]
    inbound += [(m.id, m.created_at, m.content or "", "imessage") for m in ident.list_imessages(limit=10)
                if m.direction == "inbound" and m.remote_number == phone and m.created_at > since]
    for mid, _, text, channel in sorted(inbound, key=lambda x: x[1]):
        key = str(mid)
        if key in seen or not text.strip():
            continue
        seen.add(key)
        if not db.one("insert into admin_questions (id, question) values (%s,%s) on conflict do nothing returning id",
                      key, text[:1000]):
            continue  # already answered (e.g. before a restart)
        if _shares_identity_with_verifier() and VENDOR_REPLY.match(text):
            db.one("update admin_questions set answer='(left for the verifier)' where id=%s returning id", key)
            continue
        try:
            reply = answer(text)
        except Exception as e:
            log.warning("ask failed: %s", e)
            reply = "Airlock: I couldn't look that up just now. Nothing was changed."
        try:
            (ident.send_imessage if channel == "imessage" else ident.send_text)(to=phone, text=reply)
        except Exception:
            ident.send_imessage(to=phone, text=reply)
        db.one("update admin_questions set answer=%s where id=%s returning id", reply, key)
        log.info("answered admin question %r", text[:80])


def start() -> None:
    phone = config.get("DEMO_ADMIN_PHONE")
    if not phone or (config.get("AIRLOCK_TEXT_QA") or "1") != "1":
        return
    with db.tx() as conn:
        conn.execute("""create table if not exists admin_questions (id text primary key,
                        asked_at timestamptz default now(), question text, answer text)""")

    def loop():
        since, seen = datetime.now(timezone.utc), set()
        while True:
            try:
                with Inkbox(api_key=config.need("INKBOX_API_KEY")) as ib:
                    ident = ib.get_identity(config.get("INKBOX_HANDLE", "airlock"))
                    while True:
                        _poll_once(ident, phone, seen, since)
                        time.sleep(4)
            except Exception as e:
                log.warning("ask poller error: %s", e)
                time.sleep(10)

    threading.Thread(target=loop, daemon=True, name="admin-ask").start()
    log.info("text Q&A listening for the admin's texts")
