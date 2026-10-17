"""Anomaly flags, computed deterministically from raw content + fixtures (no model votes).

Flags are advisory facts that can only escalate a decision (invariant 9). The reader's claims are used as an
extra signal, but every security-relevant flag also has a code path over the raw text.
"""
import re
import statistics
from dataclasses import dataclass, field

BANK_CHANGE = re.compile(
    r"(changed (our )?banks?|new (bank )?account|updated remittance|remittance details|moved accounts|"
    r"bank (audit|change)|remit .{0,40}(to our )?new|account is being closed|update your records)", re.I)
URGENCY = re.compile(r"\b(urgent|today|within the hour|immediately|avoid (a )?(service )?hold|asap)\b", re.I)
DUE_NOW = re.compile(r"\b(due on receipt|due immediately|payable immediately|net 0)\b", re.I)
ACCOUNT = re.compile(
    r"(?:account(?: number)?(?: ending(?: in)?)?|acct\.?)\s*(?:#|no\.?|:)?\s*([0-9][0-9\- ]{2,20}[0-9])", re.I)
ROUTING = re.compile(r"routing\s*(?:#|no\.?|number|:)?\s*(\d{9})", re.I)
AGENT_DIRECTED = re.compile(
    r"(automated|accounts payable agent|\bAI\b|assistant|language model|ignore (all |previous )?|"
    r"this instruction|overrides? (records|instructions)|route this payment|system notice)", re.I)
DATA_ASK = re.compile(
    r"(copy of (your|the) .{0,40}(contract|agreement)|payment confirmations|reply with (them|it) attached|"
    r"send (us )?(a copy|the contract|your contract)|forward .{0,40}contract)", re.I)
GENERIC_TOKENS = {"fee", "the", "and", "of", "for"}


@dataclass
class Evidence:
    """Everything flags may look at. raw_* is attacker-controlled text; registry/history/contract are trusted."""
    from_domain: str
    reply_to: str | None
    subject: str
    body: str
    attachment_text: str
    claims: dict                       # reader output grouped by field
    vendor: dict | None                # matched registry row
    lookalike_of: dict | None          # registry row the sender imitates
    contract: dict | None
    history: list[dict]
    obligation: dict | None
    settled_invoices: set[str]
    claimed_cents: int | None
    invoice_number: str | None
    flags: list[str] = field(default_factory=list)


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def lookalike(domain: str, vendors: list[dict]) -> dict | None:
    """Registered vendor this unregistered domain imitates: small edit distance, hyphen/TLD variant,
    or the vendor's brand token inside a different domain."""
    label = domain.split(".")[0].lower()
    for v in vendors:
        reg = v["registered_domain"].lower()
        if domain == reg:
            return None
        reg_label = reg.split(".")[0]
        brand = v["name"].split()[0].lower()
        if (edit_distance(label, reg_label) <= 2 or label.replace("-", "") == reg_label.replace("-", "")
                or (len(brand) >= 4 and brand in label)):
            return v
    return None


def account_last4s(text: str) -> set[str]:
    out = set()
    for m in ACCOUNT.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) >= 4:
            out.add(digits[-4:])
    return out


def _line_ok(line: str, allowed: list[str]) -> bool:
    words = set(re.findall(r"[a-z]+", line.lower()))
    return any(set(re.findall(r"[a-z]+", a.lower())) - GENERIC_TOKENS <= words for a in allowed)


def compute(e: Evidence) -> list[str]:
    f: list[str] = []
    email_text = f"{e.subject}\n{e.body}"
    target = e.vendor or e.lookalike_of

    if e.vendor is None and e.lookalike_of is None:
        f.append("NEW_VENDOR")
    if e.lookalike_of is not None:
        f.append(f"LOOKALIKE_DOMAIN:{e.from_domain}~{e.lookalike_of['registered_domain']}")
    if e.reply_to and e.reply_to.split("@")[-1].lower() != e.from_domain:
        f.append("REPLY_TO_MISMATCH")
    if URGENCY.search(email_text):
        f.append("URGENCY_LANGUAGE")

    # Remit-to: any account number in email OR attachment that isn't the registry destination.
    if target is not None:
        seen = account_last4s(email_text) | account_last4s(e.attachment_text)
        seen |= {re.sub(r"\D", "", c["value"])[-4:] for c in e.claims.get("bank_details", [])
                 if len(re.sub(r"\D", "", c["value"])) >= 4 and not ROUTING.search(c["value"])}
        if seen - {target["destination_last4"]}:
            f.append("REMIT_TO_CHANGED")
    if BANK_CHANGE.search(email_text):
        f.append("BANK_CHANGE_LANGUAGE")

    # Text inside an attachment aimed at an automated payer.
    reader_saw = any("EXTERNAL_ATTACHMENT" in c["labels"] for c in e.claims.get("instructions_to_reader", []))
    if AGENT_DIRECTED.search(e.attachment_text) or reader_saw:
        f.append("EMBEDDED_INSTRUCTIONS_IN_ATTACHMENT")
    if DATA_ASK.search(email_text) or any("EXTERNAL_EMAIL" in c["labels"] for c in e.claims.get("data_requests", [])):
        f.append("DATA_REQUEST")

    if e.contract is not None:
        if DUE_NOW.search(email_text + "\n" + e.attachment_text):
            f.append(f"TERMS_VIOLATION:due_on_receipt_vs_net{e.contract['payment_terms_days']}")
        lines = [c["value"] for c in e.claims.get("line_items", [])]
        if any(not _line_ok(l, e.contract["allowed_line_items"]) for l in lines):
            f.append("NEW_LINE_ITEM")
        if (e.contract["pricing"] == "fixed" and e.claimed_cents is not None
                and e.claimed_cents != e.contract["fixed_amount_cents"]):
            f.append("CONTRACT_PRICE_MISMATCH")

    # History deviation only matters when the claim disagrees with the approved obligation.
    if (e.obligation is not None and e.claimed_cents is not None and e.history
            and e.claimed_cents != e.obligation["amount_cents"]):
        median = statistics.median(h["amount_cents"] for h in e.history)
        if abs(e.claimed_cents - median) / median > 0.15:
            f.append("AMOUNT_DEVIATION")

    if e.vendor is not None and e.invoice_number and (
            e.invoice_number in e.settled_invoices
            or e.invoice_number in {h["invoice_number"] for h in e.history}):
        f.append("DUPLICATE_INVOICE")
    # Lookalike senders get denied regardless, so NO_PO only describes real or brand-new vendors.
    if not e.claims.get("po_number") and (e.vendor is not None or "NEW_VENDOR" in f):
        f.append("NO_PO")
    return f
