"""Anomaly flags, computed deterministically from raw content + fixtures (no model votes).

Flags are advisory facts that can only escalate a decision (invariant 9). The reader's claims are used as an
extra signal, but every security-relevant flag also has a code path over the raw text.
"""
import re
import statistics
from dataclasses import dataclass, field

BANK_CHANGE = re.compile(
    r"(changed (our )?banks?|switched (banks?|institutions?|accounts?)|new (banking|remittance) (details|info)|direct (the|all|future|this) .{0,60}(to|into) (routing|acct|account)|new (bank )?account|updated remittance|remittance details|moved accounts|"
    r"bank (audit|change)|remit .{0,40}(to our )?new|account is being closed|update your records)", re.I)
URGENCY = re.compile(r"\b(urgent|today|within the hour|immediately|avoid (a )?(service )?hold|asap)\b", re.I)
DUE_NOW = re.compile(r"\b(due on receipt|due immediately|payable immediately|net 0)\b", re.I)
ACCOUNT = re.compile(
    r"(?:account(?: number)?(?: ending(?: in)?)?|acct\.?)\s*(?:#|no\.?|:)?\s*([0-9][0-9\- ]{2,20}[0-9])", re.I)
ROUTING = re.compile(r"routing\s*(?:#|no\.?|number|:)?\s*(\d{9})", re.I)
CHANNEL_AVOIDANCE = re.compile(
    r"(don'?t call|do not call|no need to call|phones? (are|is) (down|out)|(lines|phone lines) are down|"
    r"(reply|respond|contact us) (only )?by email|email only|unable to take calls|can'?t take calls|"
    r"won'?t be reachable by phone|not reachable by phone)", re.I)
SECRECY = re.compile(r"(keep this (between us|confidential|quiet)|don'?t (tell|mention|loop in)|do not (tell|mention|share)|"
                     r"confidential (matter|request)|discreet|off the record)", re.I)
EXEC_CLAIM = re.compile(r"\b(ceo|cfo|controller|president|owner|director|vp)\b[\s\S]{0,40}\b(approved|authorized|signed off|"
                        r"asked|requested|wants)", re.I)
PAYMENT_LINK = re.compile(r"https?://\S+", re.I)
LINK_CONTEXT = re.compile(r"(update|change|confirm|verify|enter|submit)[\s\S]{0,40}(payment|bank|banking|remittance|"
                          r"account|details)|pay (here|online|now)", re.I)
PHONE = re.compile(r"(?:\+?1[\s.-]{0,2})?(?:\((\d{3})\)\s{0,2}|(\d{3})[\s.-]{1,3})(\d{3})[\s.-]{1,3}(\d{4})\b")
INVISIBLE = re.compile("[\u200b-\u200f\u2060-\u2064\ufeff\U000e0000-\U000e007f]")
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
    other_vendors: list[dict] = field(default_factory=list)  # registry rows for every vendor except the routed one
    display_name: str = ""             # From display name (attacker-controlled)
    sender_authenticated: bool | None = None  # Primitive's SPF/DKIM/DMARC verdict: a hint, never identity
    first_time_sender: bool = False    # this exact address never wrote before and isn't the registered contact
    auto_pay_limit_cents: int | None = None
    raw_text: str = ""                 # body + attachments before invisible characters were stripped
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


def _names(v: dict) -> list[str]:
    base = v["registered_domain"].split(".")[0]
    return [v["name"].lower(), v["registered_domain"].lower()] + ([base.lower()] if len(base) >= 5 else [])


def issuer_mismatch(e: Evidence) -> dict | None:
    """The attachment is another registered vendor's document (it names them and not the sender's vendor):
    the email and its invoice don't agree on who is billing. Deterministic, from the registry."""
    if e.vendor is None or not e.attachment_text.strip():
        return None
    head = e.attachment_text.lower()
    if any(n in head for n in _names(e.vendor)):
        return None
    return next((v for v in e.other_vendors if any(n in head for n in _names(v))), None)


CONFUSABLE = str.maketrans({"0": "o", "1": "l", "i": "l", "|": "l", "5": "s", "3": "e"})


def _skeleton(domain: str) -> str:
    d = domain.lower().replace("rn", "m").replace("vv", "w")
    return d.translate(CONFUSABLE)


def homoglyph_of(domain: str, vendors: list[dict]) -> dict | None:
    """A sender domain that only differs from a registered one by look-alike characters (or punycode)."""
    for v in vendors:
        reg = v["registered_domain"].lower()
        if domain.lower() != reg and (_skeleton(domain) == _skeleton(reg) or
                                      (domain.startswith("xn--") or ".xn--" in domain)):
            return v
    return None


def _name_near_miss(claimed: str, real: str) -> bool:
    import difflib
    a, b = re.sub(r"[^a-z ]", "", claimed.lower()).strip(), re.sub(r"[^a-z ]", "", real.lower()).strip()
    if not a or b in a or a in b:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def _invoice_shape(n: str) -> str:
    return re.sub(r"\d", "9", re.sub(r"[A-Za-z]", "A", n))


def compute(e: Evidence) -> list[str]:
    f: list[str] = []
    email_text = f"{e.subject}\n{e.body}"
    target = e.vendor or e.lookalike_of

    if e.vendor is None and e.lookalike_of is None:
        f.append("NEW_VENDOR")
    if e.lookalike_of is not None:
        f.append(f"LOOKALIKE_DOMAIN:{e.from_domain}~{e.lookalike_of['registered_domain']}")
    other = issuer_mismatch(e)
    if other is not None:
        f.append(f"ISSUER_MISMATCH:{other['name']}")
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

    # Text inside an attachment aimed at an automated payer. Deterministic only: the reader's own list of
    # "instructions" is shown to humans but never escalates (it varies run to run and once flagged
    # "Remit to the account on file" on a genuine invoice).
    if AGENT_DIRECTED.search(e.attachment_text):
        f.append("EMBEDDED_INSTRUCTIONS_IN_ATTACHMENT")
    if DATA_ASK.search(email_text):
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
    f += extra_signals(e)
    return f


def extra_signals(e: Evidence) -> list[str]:
    """Broader trick detection. Deterministic; escalate-only (see policy.ESCALATE_TO_HOLD for which ones hold)."""
    f: list[str] = []
    email_text = f"{e.subject}\n{e.body}"
    every = email_text + "\n" + e.attachment_text
    if e.sender_authenticated is False:  # the From domain didn't vouch for this message (spoofed From)
        f.append("SENDER_AUTH_FAILED")
    if e.raw_text and INVISIBLE.search(e.raw_text):
        f.append("INVISIBLE_UNICODE")
    if CHANNEL_AVOIDANCE.search(every):
        f.append("CHANNEL_AVOIDANCE")
    if SECRECY.search(every):
        f.append("SECRECY_LANGUAGE")
    if EXEC_CLAIM.search(every):
        f.append("EXEC_APPROVAL_CLAIM")
    if PAYMENT_LINK.search(every) and LINK_CONTEXT.search(every):
        f.append("PAYMENT_LINK")
    target = e.vendor or e.lookalike_of
    if target is not None:
        on_file = re.sub(r"\D", "", target.get("registered_phone") or "")[-10:]
        phones = {"".join(g for g in m.groups() if g) for m in PHONE.finditer(every)}
        if any(p != on_file for p in phones):
            f.append("NEW_CONTACT_IN_EMAIL")
    if e.vendor is not None:
        names = [c["value"] for c in e.claims.get("vendor_name", [])] + ([e.display_name] if e.display_name else [])
        if any(_name_near_miss(n, e.vendor["name"]) for n in names):
            f.append("VENDOR_NAME_MISMATCH")
        if e.first_time_sender:
            f.append("FIRST_TIME_SENDER")
        if e.invoice_number and e.history:
            shapes = {_invoice_shape(h["invoice_number"]) for h in e.history if h.get("invoice_number")}
            if shapes and _invoice_shape(e.invoice_number) not in shapes and not re.sub(r"-R\d+$", "", e.invoice_number) in {
                    h["invoice_number"] for h in e.history}:
                base = _invoice_shape(re.sub(r"-R\d+$", "", e.invoice_number))
                if base not in shapes:
                    f.append("INVOICE_NUMBER_PATTERN")
    if e.vendor is None:
        h = homoglyph_of(e.from_domain, e.other_vendors)
        if h is not None:
            f.append(f"HOMOGLYPH_DOMAIN:{h['registered_domain']}")
    if e.vendor is None and e.display_name:
        for v in e.other_vendors + ([e.lookalike_of] if e.lookalike_of else []):
            if v and v["name"].lower().split()[0] in e.display_name.lower():
                f.append(f"DISPLAY_NAME_MISMATCH:{v['name']}")
                break
    if (e.auto_pay_limit_cents and e.claimed_cents
            and 0.95 * e.auto_pay_limit_cents <= e.claimed_cents < e.auto_pay_limit_cents):
        f.append("THRESHOLD_DODGING")
    return f


EXPLAIN = {
    "REMIT_TO_CHANGED": "asked to pay an account that isn't the one on file",
    "BANK_CHANGE_LANGUAGE": "a 'we changed banks' story",
    "EMBEDDED_INSTRUCTIONS_IN_ATTACHMENT": "text in the attachment aimed at an automated agent",
    "LOOKALIKE_DOMAIN": "a look-alike sender domain",
    "HOMOGLYPH_DOMAIN": "a sender domain using look-alike characters",
    "REPLY_TO_MISMATCH": "replies redirected to a different address",
    "URGENCY_LANGUAGE": "pressure to pay fast",
    "CHANNEL_AVOIDANCE": "asked us not to call / email only",
    "SECRECY_LANGUAGE": "asked to keep it quiet",
    "EXEC_APPROVAL_CLAIM": "claims an executive approved it",
    "PAYMENT_LINK": "a link to 'update payment details'",
    "VENDOR_NAME_MISMATCH": "the company name is slightly off",
    "DISPLAY_NAME_MISMATCH": "a vendor's name on a non-vendor address",
    "NEW_CONTACT_IN_EMAIL": "a phone number that isn't the one on file (never used)",
    "FIRST_TIME_SENDER": "first email ever from this address",
    "INVOICE_NUMBER_PATTERN": "invoice number doesn't match the vendor's usual format",
    "THRESHOLD_DODGING": "amount just under the auto-pay limit",
    "INVISIBLE_UNICODE": "hidden invisible characters",
    "SENDER_AUTH_FAILED": "the sender's domain didn't vouch for this email (forged From)",
    "ISSUER_MISMATCH": "the attached invoice is another company's",
    "DUPLICATE_INVOICE": "an invoice that was already paid",
    "DATA_REQUEST": "asks for internal documents",
    "CONTRACT_PRICE_MISMATCH": "price doesn't match the contract",
    "AMOUNT_DEVIATION": "unusual amount for this vendor",
    "NEW_LINE_ITEM": "a line item not in the contract",
    "TERMS_VIOLATION": "payment terms that break the contract",
    "NEW_VENDOR": "not a registered vendor",
    "NO_PO": "no purchase order",
}


def explain(flags: list[str]) -> list[str]:
    out = []
    for f in flags:
        key = f.split(":")[0]
        if key == "REVIEW":
            out.append(f"reviewer noticed {f.split(':', 1)[1].lower().replace('_', ' ')}")
        elif key in EXPLAIN and EXPLAIN[key] not in out:
            out.append(EXPLAIN[key])
    return out
