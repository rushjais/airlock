"""The agent team, coordinating over Primitive email: one address per agent, one thread per case.

  intake@    opens the case thread and forwards the invoice (external-facing; no money, no internal data)
  auditor@   posts the policy engine's verdict. This is CODE (airlock.policy), not a model.
  treasurer@ a Claude agent holding Airlock's payment tools; it only ever sees the verdict, never raw email
  verifier@  contacts the vendor's phone on file (Inkbox) when a destination change is requested

Every agent email carries an HMAC signature. When our own emails come back through the webhook, a valid signature
marks them internal; anything claiming to be an agent without one is just an external claim.
"""
import hashlib
import hmac
import json
import logging
import re
import uuid

from . import config, db, primitive

log = logging.getLogger("airlock.agents")
AGENTS = ("intake", "auditor", "treasurer", "verifier")
SIG_RE = re.compile(r"airlock-sig: ([0-9a-f]{16}):([0-9a-f]{64})")


def address(local: str) -> str:
    return f"{local}@{config.INBOX_DOMAIN}"


def _sign(case_id: str, author: str, nonce: str) -> str:
    key = config.need("AIRLOCK_AGENT_SIGNING_SECRET").encode()
    return hmac.new(key, f"{case_id}|{author}|{nonce}".encode(), hashlib.sha256).hexdigest()


def verify_internal(from_address: str, to_address: str | None, body: str | None) -> tuple[str, str] | None:
    """(case_id, author) if this inbound email is one of ours, else None."""
    local, _, domain = from_address.partition("@")
    if domain != config.INBOX_DOMAIN or local not in AGENTS or not body:
        return None
    m = SIG_RE.search(body)
    case = re.search(r"\[(case-[a-z0-9-]+)\]", body)
    if not m or not case:
        return None
    nonce, sig = m.groups()
    if hmac.compare_digest(sig, _sign(case.group(1), local, nonce)):
        return case.group(1), local
    return None


def open_case(message_id: str, facts: dict, vendor_name: str | None) -> str:
    existing = db.one("select id from cases where source_message_id=%s", message_id)
    if existing:
        return existing["id"]
    slug = re.sub(r"[^a-z0-9]+", "-", (facts.get("invoice_number") or "invoice").lower()).strip("-")
    case_id = f"case-{slug}-{uuid.uuid4().hex[:4]}"
    subject = f"[{case_id}] {vendor_name or facts.get('from_domain')}: {facts.get('invoice_number') or 'inbound message'}"
    db.one("insert into cases (id, source_message_id, vendor_id, subject) values (%s,%s,%s,%s) returning id",
           case_id, message_id, facts.get("vendor_id"), subject)
    return case_id


def post(case_id: str, author: str, kind: str, body: str, data: dict | None = None, send_email: bool = True) -> None:
    """Add a message to the case thread: stored for the UI and sent as a signed email to the case address,
    CC'ing the other agents (and the admin if configured)."""
    case = db.one("select * from cases where id=%s", case_id)
    provider_id = None
    if send_email and author in AGENTS:
        nonce = uuid.uuid4().hex[:16]
        text = (f"{body}\n\n--\n{author.capitalize()} · Airlock agent team · [{case_id}]\n"
                f"airlock-sig: {nonce}:{_sign(case_id, author, nonce)}")
        cc = [address(a) for a in AGENTS if a != author]
        if config.get("DEMO_ADMIN_EMAIL"):
            cc.append(config.get("DEMO_ADMIN_EMAIL"))
        payload = {"from": f"{author.capitalize()} (Airlock) <{address(author)}>", "to": address(case_id),
                   "cc": cc, "subject": case["subject"] if kind == "open" else f"Re: {case['subject']}",
                   "body_text": text}
        if case["root_provider_message_id"] and kind != "open":
            payload["in_reply_to"] = case["root_provider_message_id"]
            payload["references"] = [case["root_provider_message_id"]]
        try:
            with primitive._client() as c:
                r = c.post("/send-mail", json=payload, headers={"Idempotency-Key": f"{case_id}-{author}-{nonce}"})
                r.raise_for_status()
                provider_id = r.json()["data"].get("id")
        except Exception as e:  # the thread in our DB is still the source of truth for the UI
            log.warning("case email failed (%s): %s", author, e)
    db.one("""insert into case_posts (case_id, author, kind, body, data, provider_id)
              values (%s,%s,%s,%s,%s,%s) returning id""",
           case_id, author, kind, body, json.dumps(data or {}, default=str), provider_id)


def record_echo(case_id: str, author: str, provider_message_id: str | None, kind_hint: str | None = None) -> None:
    """Our own email came back through the webhook: remember the root Message-ID for threading."""
    if provider_message_id and author == "intake":
        db.one("update cases set root_provider_message_id=coalesce(root_provider_message_id, %s) where id=%s returning id",
               provider_message_id, case_id)
