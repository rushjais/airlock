"""The case thread as email: who sent each message, to whom, why those recipients, and what it carried.

Primitive is the transport between agents; this view makes that visible. It mirrors what was sent, it never
adds facts: envelopes come from the agent addresses, the original email's headers, and the audit table of
outbound replies.
"""
from . import agents, config, db

NEXT_ACTION_WHY = {
    "execute_payment": "Verdict is pay, so treasurer@ may execute it (with the authorization id, never an amount or account).",
    "request_vendor_verification": "The account in the email isn't the one on file, so verifier@ must check with the "
                                   "vendor's phone already on file before anything moves.",
    "reply_in_thread:ask_vendor_to_explain_or_reissue": "The amount doesn't match the approved order, so treasurer@ "
                                                        "asks the vendor to explain or reissue.",
    "reply_in_thread:ask_vendor_to_resend": "The attached invoice is another company's, so treasurer@ asks the sender "
                                            "for their own invoice.",
    "reply_in_thread:already_paid": "This order was already paid, so treasurer@ tells the vendor. No second payment.",
    "notify_admin": "Nothing for the agents to pay; the admin gets an FYI.",
    "request_obligation_approval": "No approved order covers it, so a human must approve the purchase first.",
    "request_vendor_onboarding": "Unknown sender: onboarding a vendor needs a human.",
}

AUTHOR_WHY = {
    "intake": "Opened the case thread and handed the claims to the team. intake@ faces the outside world and has no "
              "payment tools.",
    "treasurer": "Acted on the auditor's verdict with Airlock's tools. treasurer@ never sees the raw email.",
    "verifier": "Talks to the vendor's phone already on file (Inkbox), never a number from the email.",
    "vendor": "Mirror of Corvid's own books: a separate company with its own agent and ledger.",
    "unverified": "Claims to be one of our agents but carries no valid signature, so it is only an outside claim.",
    "admin": "Admin comment. A reply is never authorization.",
}


def _agent_envelope(case: dict, author: str, kind: str, data: dict) -> dict:
    dom = config.INBOX_DOMAIN
    why = AUTHOR_WHY.get(author, "")
    if author == "auditor":
        why = NEXT_ACTION_WHY.get(data.get("next_action") or "", "Posted the policy verdict (deterministic code).")
    cc = [agents.address(a) for a in agents.AGENTS if a != author]
    return {"from": agents.address(author) if author in agents.AGENTS else None,
            "to": f"{case['id']}@{dom}" if author in agents.AGENTS else None,
            "cc": cc if author in agents.AGENTS else [],
            "subject": case["subject"] if kind == "open" else f"Re: {case['subject']}",
            "signed": author in agents.AGENTS, "why": why}


def posts(case: dict) -> list[dict]:
    mid = case["source_message_id"]
    out: list[dict] = []
    m = db.one("select * from messages where id=%s", mid) if mid else None
    raw = db.one("select body_text, attachments from message_raw where message_id=%s", mid) if mid else None
    atts = [{"name": a.get("filename"), "label": "EXTERNAL_ATTACHMENT", "note": "quarantined; only the reader sees it"}
            for a in (raw["attachments"] if raw else []) or []]
    if m:
        out.append({"author": "inbound", "kind": "inbound", "created_at": m["received_at"],
                    "body": ((raw or {}).get("body_text") or "").split("\n\n--")[0].strip()[:700],
                    "data": {}, "envelope": {
                        "from": m["from_address"], "to": m["to_address"] or config.AGENT_INBOX,
                        "cc": [], "reply_to": m["reply_to"], "subject": m["subject"], "signed": False,
                        "attachments": atts,
                        "why": "Untrusted inbound email. Everything in it is a claim; it can never authorize a payment."}})
    for p in db.all_rows("select author, kind, body, data, created_at, provider_id from case_posts where case_id=%s "
                         "order by created_at", case["id"]):
        env = _agent_envelope(case, p["author"], p["kind"], p["data"] or {})
        if p["kind"] == "open":
            env["attachments"] = atts
        env["delivered"] = bool(p["provider_id"])
        out.append({**p, "envelope": env})
    if mid:
        for o in db.all_rows("""select channel, to_address, template, body, created_at, provider_id from outbound_messages
                                where in_reply_to_message_id=%s order by created_at""", mid):
            out.append({"author": "outbound", "kind": "email_out", "created_at": o["created_at"],
                        "body": o["body"], "data": {"template": o["template"]}, "envelope": {
                            "from": config.AGENT_INBOX, "to": o["to_address"], "cc": [],
                            "subject": f"Re: {m['subject'] if m else ''}", "signed": False,
                            "delivered": bool(o["provider_id"]), "template": o["template"],
                            "why": "Reply to the vendor in the original thread, from a fixed template (no free-form "
                                   "model text goes to outsiders)."}})
    out.sort(key=lambda x: x["created_at"])
    for p in out:
        p.pop("provider_id", None)
    return out
