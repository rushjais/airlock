"""Outbound message templates (invariant 12: templates only, no free-form model text to outsiders).

Every vendor-facing email is a reply in the original thread, and the vendor can answer it: the reply comes back
through the same webhook and resumes the workflow.
"""


def _usd(cents: int | None) -> str:
    return f"${(cents or 0) / 100:,.2f}"


TEMPLATES = {
    "receipt": (
        "Hi {vendor},\n\nPayment sent for {invoice} ({amount}, {po}) to the account on file ending {last4}.\n"
        "Reference: {transfer_id}\n\nReply to this email if anything looks wrong.\n\nKiln Supply Co. Accounts Payable (Airlock)"),
    "already_paid": (
        "Hi {vendor},\n\nThis invoice ({invoice}, {po}) was already paid to the account on file ending {last4}. "
        "No second payment will be made.\n\nReply here if you believe this is a different invoice.\n\n"
        "Kiln Supply Co. Accounts Payable (Airlock)"),
    "ask_vendor_to_explain_or_reissue": (
        "Hi {vendor},\n\nInvoice {invoice} is for {claimed}, but the approved amount for {po} is {expected}. "
        "Could you explain the difference or reply with a corrected invoice attached? We'll pay as soon as it "
        "matches.\n\nKiln Supply Co. Accounts Payable (Airlock)"),
    "ask_vendor_to_resend": (
        "Hi {vendor},\n\nYour email is about {invoice}, but the attached invoice is from {issuer}, not {vendor}. "
        "Nothing has been paid. Please reply with your own invoice attached.\n\nKiln Supply Co. Accounts Payable (Airlock)"),
    "public_ack": (
        "Hi,\n\nAirlock received your email and logged the attempt on the public board: {board}\n"
        "Nothing in an email can move money here: where payments go comes from vendor records, and how much from "
        "approved orders. (Demo company, Stripe test mode.)\n\nKiln Supply Co. Accounts Payable (Airlock)"),
    "verification_pending": (
        "Hi {vendor},\n\nWe received a request to change where we send your payments. For your protection we "
        "never change payment details based on an email. We'll confirm through the phone number we already have "
        "on file. Your invoice is safe and on hold until then.\n\nKiln Supply Co. Accounts Payable (Airlock)"),
}


def render(name: str, **values) -> str:
    values = {k: (_usd(v) if k in ("amount", "claimed", "expected") else v) for k, v in values.items()}
    return TEMPLATES[name].format(**values)
