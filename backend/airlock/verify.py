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
    text = (f"Kiln Supply Co. (Airlock): we received an email asking to send {vendor['name']}'s payments to a new "
            f"account ({cr['new_destination']}). Did you request this?\n"
            f"Reply YES {code} to confirm, or NO if you did not.")
    phone = vendor["registered_phone"]  # the phone on file, never a number from the email
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
        if r and r["answer"] != "invalid":
            return r
        time.sleep(every_s)
    return None
