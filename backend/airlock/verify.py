"""Out-of-band vendor verification through the phone already on file (invariant 7), via Inkbox.

SMS when the recipient has opted in; otherwise iMessage (the Inkbox number is iMessage-reachable too).
A YES needs the 6-digit code (so nobody else can confirm a change); a NO needs nothing (fail-safe direction).
The vendor's reply is evidence. Only an admin's authenticated click can change the registry.
"""
import hashlib
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from inkbox import Inkbox

from . import config, db

log = logging.getLogger("airlock.verify")
OTP_TTL = timedelta(minutes=10)


def _identity(ib: Inkbox):
    return ib.get_identity(config.get("INKBOX_HANDLE", "airlock"))


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def send_code(change_request_id: str) -> dict:
    cr = db.one("select * from change_requests where id=%s", change_request_id)
    vendor = db.one("select * from vendors where id=%s", cr["vendor_id"])
    if cr["status"] != "pending_verification":
        return {"ok": False, "reason": f"change_request_{cr['status']}"}
    code = f"{secrets.randbelow(10**6):06d}"
    possessive = vendor["name"] + ("'" if vendor["name"].endswith("s") else "'s")
    new = cr["new_destination"].replace("account(s) ending", "an account ending")
    text = (f"Kiln Supply Co. (Airlock): we received an email asking to send {possessive} payments to {new}. "
            f"Did you request this?\nReply YES {code} to confirm, or NO if you did not.")
    phone = vendor["registered_phone"]  # the phone on file, never a number from the email
    if (config.get("AIRLOCK_VERIFY_CHANNEL") or "text") == "call":
        return _place_call(cr, vendor, new, phone)
    channel = "sms"
    with Inkbox(api_key=config.need("INKBOX_API_KEY")) as ib:
        ident = _identity(ib)
        try:
            ident.send_text(to=phone, text=text)
        except Exception as e:
            if "not_opted_in" not in str(e) and "opted" not in str(e):
                raise
            channel = "imessage"
            ident.send_imessage(to=phone, text=text)
    sent_at = datetime.now(timezone.utc)
    db.one("""update change_requests set otp_hash=%s, otp_expires_at=%s, otp_used=false,
              evidence = evidence || %s::jsonb where id=%s returning id""",
           _hash(code), sent_at + OTP_TTL,
           json.dumps({"verification_sent_at": sent_at.isoformat(), "channel": channel, "phone_on_file": phone}),
           change_request_id)
    return {"ok": True, "channel": channel, "phone_on_file_last4": phone[-4:], "sent_at": sent_at.isoformat()}


def _place_call(cr: dict, vendor: dict, new: str, phone: str) -> dict:
    """Inkbox Voice AI calls the phone on file and asks a yes/no question. The answer is evidence only."""
    ob = db.one("select po_number, amount_cents from obligations where id=%s", cr["held_obligation_id"]) \
        if cr["held_obligation_id"] else None
    inv = db.one("select value from claims where message_id=%s and field='invoice_number' limit 1", cr["source_message_id"])
    what = (f"invoice {inv['value'] if inv else ''} for ${ob['amount_cents'] / 100:,.2f} ({ob['po_number']})" if ob
            else "their invoices")
    new_digits = " ".join((cr.get("new_last4") or "").strip())
    reason = (
        "You are the automated accounts-payable assistant for Kiln Supply Co., calling the phone number Kiln already "
        f"has on file for {vendor['name']}. Follow this script closely.\n"
        f"1. Say: \"Hi, this is the automated accounts-payable assistant for Kiln Supply Company. We received an email "
        f"that appears to be from {vendor['name']}, asking us to send the payment for {what} to a new bank account "
        f"ending in {new_digits or 'a new number'}. Did {vendor['name']} request this change? Please answer yes or no.\"\n"
        "2. Wait for their answer. If it is not a clear yes or no, ask once more: \"Just to confirm, did your company "
        "request a change to your bank account, yes or no?\"\n"
        "3. Then say: \"Thank you. Kiln never changes payment details based on an email alone.\" and end the call.\n"
        "Do not ask for, read out, or accept any bank details. Do not promise or discuss payment timing. Keep it short.")
    with Inkbox(api_key=config.need("INKBOX_API_KEY")) as ib:
        call = _identity(ib).place_call(to_number=phone, mode="hosted_agent", reason=reason, on_voicemail="hang_up")
    sent_at = datetime.now(timezone.utc)
    db.one("""update change_requests set otp_hash=null, otp_expires_at=%s, otp_used=false,
              evidence = evidence || %s::jsonb where id=%s returning id""",
           sent_at + OTP_TTL, json.dumps({"verification_sent_at": sent_at.isoformat(), "channel": "voice call",
                                          "call_id": str(call.id), "phone_on_file": phone}), cr["id"])
    return {"ok": True, "channel": "voice call", "phone_on_file_last4": phone[-4:], "sent_at": sent_at.isoformat(),
            "call_id": str(call.id)}


NO_WORDS = re.compile(r"\b(no|nope|didn'?t|did not|not us|never|that'?s not|wasn'?t us)\b", re.I)
YES_WORDS = re.compile(r"\b(yes|yeah|yep|correct|we did|that'?s right|confirm(ed)?)\b", re.I)


def _check_call(cr: dict) -> dict | None:
    call_id = cr["evidence"]["call_id"]
    with Inkbox(api_key=config.need("INKBOX_API_KEY")) as ib:
        ident = _identity(ib)
        call = next((c for c in ident.list_calls(limit=20) if str(c.id) == call_id), None)
        if call is None or not getattr(call, "ended_at", None):
            return None  # still ringing / talking
        turns = ident.list_transcripts(call_id)
    vendor_said = " ".join((t.text or "") for t in turns
                           if str(getattr(t, "party", "")).lower() not in ("agent", "local", "assistant", "ai"))
    transcript = "\n".join(f"{getattr(t, 'party', '?')}: {t.text}" for t in turns)
    if not vendor_said.strip():
        return {"answer": "unclear", "reply": "(no answer on the call)", "transcript": transcript}
    no, yes = bool(NO_WORDS.search(vendor_said)), bool(YES_WORDS.search(vendor_said))
    answer = "no" if no and not yes else "yes" if yes and not no else "unclear"
    return {"answer": answer, "reply": vendor_said.strip()[:300], "transcript": transcript}


def _replies_since(phone: str, since: datetime) -> list[str]:
    out = []
    with Inkbox(api_key=config.need("INKBOX_API_KEY")) as ib:
        ident = _identity(ib)
        for t in ident.list_texts(limit=20):
            if t.direction == "inbound" and t.remote_phone_number == phone and t.created_at > since:
                out.append((t.created_at, t.text or ""))
        for m in ident.list_imessages(limit=20):
            if m.direction == "inbound" and m.remote_number == phone and m.created_at > since:
                out.append((m.created_at, m.content or ""))
    return [txt for _, txt in sorted(out)]


def check_reply(change_request_id: str) -> dict | None:
    """Look for the vendor's answer. Returns {'answer': 'no'|'yes', ...} or None if nothing yet."""
    cr = db.one("select * from change_requests where id=%s", change_request_id)
    if cr["evidence"].get("channel") == "voice call":
        return _check_call(cr)
    vendor = db.one("select * from vendors where id=%s", cr["vendor_id"])
    since = datetime.fromisoformat(cr["evidence"]["verification_sent_at"])
    for text in _replies_since(vendor["registered_phone"], since):
        t = text.strip().lower()
        if re.match(r"^(no|nope|not us|we did not|didn't)\b", t):
            return {"answer": "no", "reply": text}
        m = re.match(r"^yes\s+(\d{6})\b", t)
        if m:
            fresh = cr["otp_expires_at"] and cr["otp_expires_at"] > datetime.now(timezone.utc)
            if fresh and not cr["otp_used"] and _hash(m.group(1)) == cr["otp_hash"]:
                return {"answer": "yes", "reply": "YES ******"}
            return {"answer": "invalid", "reply": "YES with wrong/expired code"}
    return None


def wait_for_reply(change_request_id: str, timeout_s: int = 600, every_s: float = 3) -> dict | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = check_reply(change_request_id)
        except Exception as e:
            log.warning("inkbox poll failed: %s", e)
            r = None
        if r and r["answer"] not in ("invalid",):
            return r
        time.sleep(every_s)
    return None
