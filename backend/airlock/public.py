"""'Try to steal from Airlock': anyone may email the AP inbox. Guardrails and the public attempts board.

Safety does not depend on any of this (nothing an email says can move money); these limits are about cost, noise
and privacy:
  - kill switch: AIRLOCK_INTAKE_PAUSED=1 stores mail but processes nothing
  - per-sender rate limit (AIRLOCK_PUBLIC_PER_SENDER_HOUR, default 6)
  - model budget: past AIRLOCK_READER_HOURLY_CAP emails/hour (or 4 per sender) the reader falls back to regex
  - unregistered senders get no Treasurer turn and at most one templated acknowledgment per day
  - at most 3 vendor-phone verifications per hour
  - the board never shows email addresses or raw text: a handle, what was attempted, and the rule that caught it

Attempts live in their own schema so a demo reset doesn't erase the record.
"""
import hashlib
import re

from . import config, db

SCHEMA = "airlock_public"


def enabled() -> bool:
    return (config.get("AIRLOCK_PUBLIC_MODE") or "") == "1"


def paused() -> bool:
    return (config.get("AIRLOCK_INTAKE_PAUSED") or "") == "1"


def ensure() -> None:
    with db.tx() as conn:
        conn.execute(f"create schema if not exists {SCHEMA}")
        conn.execute(f"""create table if not exists {SCHEMA}.attempts (
            id bigserial primary key, at timestamptz not null default now(), handle text, sender_hash text,
            tried text[] not null default '{{}}', rule text, decision text, claimed_cents bigint,
            registered_vendor boolean, case_id text)""")


def handle(from_header: str | None, address: str) -> str:
    name = re.sub(r"<.*?>", "", from_header or "").strip().strip('"').split(" ")[0]
    name = re.sub(r"[^A-Za-z0-9._-]", "", name)[:16]
    if not name or "@" in (from_header or "").split("<")[0]:
        name = ""
    return name or "anon-" + hashlib.sha256(address.lower().encode()).hexdigest()[:4]


def _sender_hash(address: str) -> str:
    return hashlib.sha256(("airlock|" + address.lower()).encode()).hexdigest()[:12]


def gate(message_id: str) -> str | None:
    """Why this email should not be processed right now, or None."""
    if paused():
        return "intake_paused"
    if not enabled():
        return None
    m = db.one("select from_address from messages where id=%s", message_id)
    n = db.one("""select count(*) n from messages where from_address=%s and received_at > now() - interval '1 hour'""",
               m["from_address"])["n"]
    return "rate_limited" if n > int(config.get("AIRLOCK_PUBLIC_PER_SENDER_HOUR") or 6) else None


def model_budget_ok(message_id: str) -> bool:
    if not enabled():
        return True
    m = db.one("select from_address from messages where id=%s", message_id)
    total = db.one("select count(*) n from messages where received_at > now() - interval '1 hour'")["n"]
    mine = db.one("""select count(*) n from messages where from_address=%s and received_at > now() - interval '1 hour'""",
                  m["from_address"])["n"]
    return total <= int(config.get("AIRLOCK_READER_HOURLY_CAP") or 150) and mine <= 4


MONEY = re.compile(r"\$\s?([0-9][0-9,]*(?:\.\d{2})?)")
ACCT = re.compile(r"(?:account|acct|routing|iban)[^\n]{0,30}?([0-9][0-9 -]{3,})", re.I)


def regex_read(message_id: str) -> None:
    """Budget fallback: extract claims with regexes instead of the model. Labeled as such; security checks are
    deterministic either way."""
    m = db.one("select subject from messages where id=%s", message_id)
    raw = db.one("select body_text, attachments from message_raw where message_id=%s", message_id)
    text = (m["subject"] or "") + "\n" + (raw["body_text"] or "") + "\n" + "\n".join(
        a.get("text") or "" for a in raw["attachments"] or [])
    claims: list[tuple[str, str]] = []
    inv = re.search(r"\b(INV-[A-Z0-9-]+|BF-\d+)\b", text, re.I) or re.search(r"invoice\s*#?\s*([A-Z0-9-]{3,})", text, re.I)
    if inv:
        claims.append(("invoice_number", inv.group(1).upper()))
    po = re.search(r"\bPO[-\s]?(\d+)\b", text, re.I)
    if po:
        claims.append(("po_number", f"PO-{po.group(1)}"))
    due = re.search(r"(?:total|amount due|due)[^\n$]{0,20}\$\s?([0-9][0-9,]*(?:\.\d{2})?)", text, re.I)
    amt = due.group(1) if due else (MONEY.findall(text) or [None])[0]
    if amt:
        claims.append(("amount_cents", str(round(float(amt.replace(",", "")) * 100))))
    claims += [("bank_details", a.strip()) for a in ACCT.findall(text)[:4]]
    claims.append(("summary", "Extracted by regex (model budget reached for this hour)."))
    with db.tx() as conn:
        conn.execute("delete from claims where message_id=%s", (message_id,))
        for field, value in claims:
            conn.execute("insert into claims (message_id, field, value, labels) values (%s,%s,%s,%s)",
                         (message_id, field, value, ["EXTERNAL_EMAIL", "REGEX_READER"]))


TRIED = {
    "REMIT_TO_CHANGED": "redirect the payment to a new account",
    "BANK_CHANGE_LANGUAGE": "'we changed banks' story",
    "EMBEDDED_INSTRUCTIONS_IN_ATTACHMENT": "hidden instructions aimed at the agent",
    "LOOKALIKE_DOMAIN": "lookalike vendor domain",
    "REPLY_TO_MISMATCH": "Reply-To swap",
    "URGENCY_LANGUAGE": "urgency pressure",
    "DUPLICATE_INVOICE": "re-bill an invoice already paid",
    "CONTRACT_PRICE_MISMATCH": "overcharge vs. contract",
    "AMOUNT_DEVIATION": "unusual amount",
    "NEW_LINE_ITEM": "sneak in a new line item",
    "DATA_REQUEST": "extract internal documents",
    "ISSUER_MISMATCH": "another company's invoice",
    "TERMS_VIOLATION": "rush payment against terms",
    "NEW_VENDOR": "invoice from an unknown sender",
    "NO_PO": "no purchase order",
    "FORGED_AGENT_SENDER": "impersonate an Airlock agent",
    "UNSIGNED_AGENT_MAIL": "message an Airlock agent directly",
}


def record(message_id: str, verdict: dict | None, case_id: str | None, rule: str | None = None) -> None:
    if not enabled():
        return
    m = db.one("select from_address, subject from messages where id=%s", message_id)
    hdr = db.one("select from_header from message_raw where message_id=%s", message_id)
    flags = (verdict or {}).get("flags") or []
    tried = sorted({TRIED[f.split(":")[0]] for f in flags if f.split(":")[0] in TRIED})
    facts = (verdict or {}).get("facts") or {}
    db.one(f"""insert into {SCHEMA}.attempts (handle, sender_hash, tried, rule, decision, claimed_cents,
               registered_vendor, case_id) values (%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
           handle((hdr or {}).get("from_header"), m["from_address"]), _sender_hash(m["from_address"]), tried,
           rule or (verdict or {}).get("reason"), (verdict or {}).get("decision"), facts.get("claimed_cents"),
           bool(facts.get("vendor_id")), case_id)


def verification_budget_ok() -> bool:
    if not enabled():
        return True
    n = db.one("""select count(*) n from change_requests where evidence ? 'contact_claimed_at'
                  and (evidence->>'contact_claimed_at')::timestamptz > now() - interval '1 hour'""")["n"]
    return n < 3


def board() -> dict:
    ensure()
    rows = db.all_rows(f"""select at, handle, tried, rule, decision, claimed_cents, registered_vendor
                          from {SCHEMA}.attempts order by at desc limit 100""")
    totals = db.one(f"""select count(*) attempts_count, count(distinct sender_hash) people,
                       coalesce(sum(claimed_cents) filter (where decision is distinct from 'pay'), 0) asked_cents
                       from {SCHEMA}.attempts""")
    stolen = db.one("""select coalesce(sum(amount_cents),0) c from payment_authorizations where status='succeeded'
                       and stripe_account_id not in (select stripe_account_id from vendors)""")["c"]
    return {"attempts": rows, **totals, "stolen_cents": stolen, "inbox": config.AGENT_INBOX}
