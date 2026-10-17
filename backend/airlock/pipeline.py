"""What happens to one inbound email, end to end, as a case thread worked by the agent team.

ingest (stored) -> reader -> Intake opens case -> Auditor (policy code) verdict -> Treasurer (Claude) acts via tools
-> [Verifier phone check -> Auditor re-verdict -> Treasurer] -> templated reply to the vendor.
This module never sees the Stripe key; payments go through the executor process by authorization_id only.
"""
import logging
import threading

import httpx

from . import agents, config, db, policy, primitive, reader, treasurer, verify
from .templates import render

log = logging.getLogger("airlock.pipeline")


def _usd(c) -> str:
    return f"${(c or 0) / 100:,.2f}"


def execute_payment(authorization_id: str) -> dict:
    r = httpx.post(f"{config.EXECUTOR_URL}/execute", json={"authorization_id": authorization_id}, timeout=60)
    return r.json()


def _reply(message_id: str, template: str, **values) -> dict:
    msg = db.one("select provider_message_id, from_address from messages where id=%s", message_id)
    body = render(template, **values)
    provider_id = None
    if not msg["provider_message_id"].startswith("replay:"):
        try:
            sent = primitive.reply(msg["provider_message_id"], body, idempotency_key=f"airlock-{message_id}-{template}")
            provider_id = sent.get("id")
        except httpx.HTTPError as e:
            log.warning("reply failed: %s", e)
    db.one("""insert into outbound_messages (channel, to_address, template, in_reply_to_message_id, provider_id, body)
              values ('email',%s,%s,%s,%s,%s) returning id""", msg["from_address"], template, message_id, provider_id, body)
    return {"ok": True, "sent_to": msg["from_address"], "template": template}


# ---------- what each agent says (deterministic text; the Auditor is code) ----------

def _intake_text(message_id: str, facts: dict) -> str:
    m = db.one("select from_address, subject from messages where id=%s", message_id)
    c = reader.claims_for(message_id)
    def v(f):
        vals = c.get(f) or []
        return f"{vals[0]['value']} ({'attachment' if 'EXTERNAL_ATTACHMENT' in vals[0]['labels'] else 'email'})" if vals else "-"
    lines = [f"New email from {m['from_address']}: \"{m['subject']}\".",
             f"It claims: invoice {v('invoice_number')}, {v('po_number')}, amount "
             f"{_usd(facts.get('claimed_cents')) if facts.get('claimed_cents') else '-'}, vendor {v('vendor_name')}."]
    if c.get("bank_details"):
        lines.append("It mentions bank details: " + "; ".join(x["value"] for x in c["bank_details"]) + ".")
    if c.get("instructions_to_reader"):
        lines.append("It contains instructions aimed at us: " + " / ".join(f"\"{x['value'][:120]}\"" for x in c["instructions_to_reader"]) + ".")
    lines.append("These are unverified claims. I have no payment access; Auditor, over to you.")
    return "\n".join(lines)


AUDITOR_REASONS = {
    "matched_accepted_obligation": "matches approved {po} for {obligation} and the vendor's account on file",
    "destination_change_requested": "the email asks to pay a different account than the one on file. Holding: "
                                    "a bank change is only accepted after the vendor confirms on the phone already on file",
    "sender_not_registered": "the sender is not a registered vendor (it imitates {imitates})",
    "outbound_recipient_not_registered": "an unregistered sender is asking for internal documents",
    "amount_mismatch": "it asks for {claimed} but the approved amount is {obligation}",
    "obligation_already_settled": "{po} was already paid",
    "obligation_not_accepted": "{po} is approved but the goods haven't been received yet",
    "unknown_vendor": "this vendor is not in our registry",
    "change_rejected_paying_registry_destination": "the vendor confirmed they did NOT change banks. Paying the "
                                                   "approved {obligation} to the account already on file",
}


def _auditor_text(verdict: dict) -> str:
    f = verdict["facts"]
    reason = verdict["reason"]
    tmpl = AUDITOR_REASONS.get(reason) or (f"standing limit covers it ({f.get('standing_limit')})"
                                          if reason.startswith("within_standing_limit") else reason.replace("_", " "))
    detail = tmpl.format(po=f.get("po_number") or "the PO", obligation=_usd(f.get("obligation_cents")),
                         claimed=_usd(f.get("claimed_cents")), imitates=f.get("imitates", "a vendor"))
    head = {"pay": "APPROVED", "hold": "HOLD", "deny": "DENIED", "approval_required": "NEEDS APPROVAL",
            "pending_approval": "NEEDS APPROVAL"}[verdict["decision"]]
    out = f"{head}: {detail}."
    if verdict["flags"]:
        out += f"\nFlags: {', '.join(verdict['flags'])}."
    out += f"\nNext action: {verdict['next_action']}" + (
        f" (authorization {verdict['authorization_id'][:8]})" if verdict.get("authorization_id") else "")
    return out + "\n(Airlock policy is deterministic code; no model voted on this.)"


# ---------- the case flow ----------

def _handlers(case_id: str, message_id: str, verdict: dict) -> dict:
    facts = verdict["facts"]
    vendor = db.one("select * from vendors where id=%s", facts.get("vendor_id")) if facts.get("vendor_id") else None
    common = dict(vendor=vendor["name"] if vendor else "there", invoice=facts.get("invoice_number") or "your invoice",
                  po=facts.get("po_number") or "this charge", last4=vendor["destination_last4"] if vendor else "")

    def do_execute(authorization_id: str) -> dict:
        result = execute_payment(authorization_id)
        if result.get("ok"):  # report the registry label the verdict used, not a raw Stripe id
            result["destination"] = facts.get("destination") or result.get("destination")
            _resolve_superseded(case_id, facts.get("obligation_id"), facts.get("invoice_number"))
            _reply(message_id, "receipt", amount=result["amount_cents"], transfer_id=result["transfer_id"], **common)
            db.one("update cases set status='paid' where id=%s returning id", case_id)
        return result

    def do_verify(change_request_id: str) -> dict:
        sent = verify.send_code(change_request_id)
        if sent.get("ok"):
            _reply(message_id, "verification_pending", **common)
            agents.post(case_id, "verifier", "action",
                        f"Sent a verification request to the phone {common['vendor']} has on file (ending "
                        f"{sent['phone_on_file_last4']}) by {sent['channel']}. Not the number in the email. "
                        f"Waiting for their answer; the invoice stays on hold.", sent)
            threading.Thread(target=_await_vendor, args=(case_id, message_id, change_request_id), daemon=True).start()
        return sent

    def do_reply(template: str) -> dict:
        return _reply(message_id, template, claimed=facts.get("claimed_cents"), expected=facts.get("obligation_cents"),
                      **common)

    def do_escalate(reason: str) -> dict:
        db.one("update cases set status='needs_approval' where id=%s returning id", case_id)
        agents.post(case_id, "treasurer", "warning", f"Escalated to admin: {reason}", send_email=False)
        return {"ok": True}

    return {"execute_payment": do_execute, "request_vendor_verification": do_verify,
            "reply_to_vendor": do_reply, "escalate_to_admin": do_escalate}


def _resolve_superseded(paid_case_id: str, obligation_id: str | None, invoice: str | None) -> None:
    """A held case for the same obligation (e.g. the original overpriced invoice) is resolved by this payment."""
    if not obligation_id:
        return
    held = db.all_rows("""select c.id from cases c join decisions d on d.message_id=c.source_message_id
                          where c.status in ('held','needs_approval') and c.id <> %s and d.facts->>'obligation_id'=%s""",
                       paid_case_id, obligation_id)
    for c in {r["id"] for r in held}:
        db.one("update cases set status='resolved' where id=%s returning id", c)
        agents.post(c, "auditor", "verdict", f"RESOLVED: {obligation_id} was paid by {invoice or 'a later invoice'} "
                    f"(case {paid_case_id}). Nothing more to pay here.", send_email=False)


def _treasurer_turn(case_id: str, message_id: str, verdict: dict) -> dict:
    t = treasurer.run(case_id, verdict, _handlers(case_id, message_id, verdict))
    paid = next((a["result"] for a in t["actions"] if a["tool"] == "execute_payment" and a["result"].get("ok")), None)
    data = {"actions": t["actions"]}
    summary = t["summary"] or "Done."
    if paid:
        summary += f"\nTransfer {paid['transfer_id']}: {_usd(paid['amount_cents'])} to {paid['destination']}."
    agents.post(case_id, "treasurer", "result", summary, data)
    status = {"pay": "paid" if paid else "open", "hold": "held", "deny": "denied"}.get(verdict["decision"], "needs_approval")
    db.one("update cases set status=%s where id=%s and status <> 'paid' returning id", status, case_id)
    return t


def _await_vendor(case_id: str, message_id: str, change_request_id: str) -> None:
    reply = verify.wait_for_reply(change_request_id)
    if reply is None:
        agents.post(case_id, "verifier", "result", "No answer from the phone on file within 10 minutes. "
                    "Still holding; escalating to admin.")
        db.one("update cases set status='needs_approval' where id=%s returning id", case_id)
        return
    if reply["answer"] == "no":
        heard = "said on the call" if reply.get("transcript") else "answered from the phone on file"
        transcript = ("\nCall transcript:\n" + reply["transcript"].replace("local:", "Airlock:").replace("remote:", "Vendor:")
                      if reply.get("transcript") else "")
        agents.post(case_id, "verifier", "result",
                    f"The vendor {heard}: \"{reply['reply']}\". They did NOT change banks, "
                    f"so the email's account change is rejected.{transcript}", {"transcript": reply.get("transcript")})
        verdict = policy.resolve_change_rejected(change_request_id, {"vendor_reply": reply["reply"], "reply": reply["reply"]})
        agents.post(case_id, "auditor", "verdict", _auditor_text(verdict), verdict)
        if verdict["next_action"] == "execute_payment":
            _treasurer_turn(case_id, message_id, verdict)
        _admin_brief(case_id, change_request_id, "no")
    elif reply["answer"] != "yes":
        agents.post(case_id, "verifier", "result",
                    f"Couldn't get a clear answer from the phone on file (\"{reply.get('reply', '')[:160]}\"). "
                    "Still holding; escalating to an admin.", reply)
        db.one("update cases set status='needs_approval' where id=%s returning id", case_id)
        _admin_brief(case_id, change_request_id, "unclear")
    else:
        db.one("update change_requests set status='verified', otp_used=true where id=%s returning id", change_request_id)
        agents.post(case_id, "verifier", "result",
                    "The vendor confirmed the change with the correct one-time code. That's evidence, not authority: "
                    "an admin must approve the new account on the authenticated screen before any payment goes there.")
        db.one("update cases set status='needs_approval' where id=%s returning id", case_id)
        _notify_admin(case_id, change_request_id)


def _text_admin(text: str) -> bool:
    phone = config.get("DEMO_ADMIN_PHONE")
    if not phone:
        return False
    try:
        from inkbox import Inkbox
        with Inkbox(api_key=config.need("INKBOX_API_KEY")) as ib:
            ident = ib.get_identity(config.get("INKBOX_HANDLE", "airlock"))
            try:
                ident.send_text(to=phone, text=text)
            except Exception:
                ident.send_imessage(to=phone, text=text)
        return True
    except Exception as e:
        log.warning("admin text failed: %s", e)
        return False


def _admin_brief(case_id: str, change_request_id: str, outcome: str) -> None:
    """Tell the admin what the agents figured out. Only 'needs you' when a human decision is truly required."""
    cr = db.one("""select c.*, v.name, v.destination_last4 from change_requests c join vendors v on v.id=c.vendor_id
                   where c.id=%s""", change_request_id)
    ob = db.one("select amount_cents, po_number from obligations where id=%s", cr["held_obligation_id"]) \
        if cr["held_obligation_id"] else None
    amt = f"${ob['amount_cents'] / 100:,.2f}" if ob else "the invoice"
    how = "called" if (cr["evidence"] or {}).get("channel") == "voice call" else "texted"
    if outcome == "no":
        text = (f"Airlock FYI (no action needed): an email asked to move {cr['name']}'s payments to an account ending "
                f"{cr['new_last4']}. I {how} {cr['name']}'s phone on file and they said they did NOT request it. "
                f"I rejected the change and paid {amt} to the account on file (ending {cr['destination_last4']}).")
    else:
        text = (f"Airlock needs you: an email asked to move {cr['name']}'s payments to an account ending "
                f"{cr['new_last4']}. I {how} their phone on file but couldn't get a clear answer. {amt} is on hold and "
                f"nothing was paid. Please call {cr['name']} yourself before approving anything.")
    if _text_admin(text):
        agents.post(case_id, "verifier", "action", "Briefed the admin by text: " + text.split(": ", 1)[1][:220],
                    send_email=False)


def _notify_admin(case_id: str, change_request_id: str) -> None:
    """Text the admin a signed approval link. The link is the authentication; a reply to the text never is."""
    from . import approvals
    phone = config.get("DEMO_ADMIN_PHONE")
    link = approvals.approval_link(change_request_id)
    log.info("approval link for %s: %s", change_request_id, link)
    if not phone:
        return
    cr = db.one("select c.*, v.name from change_requests c join vendors v on v.id=c.vendor_id where c.id=%s", change_request_id)
    text = (f"Airlock needs you: an email asked to move {cr['name']}'s payments to an account ending "
            f"{cr['new_last4']}. I checked with {cr['name']} on their phone on file and they confirmed it with the "
            f"one-time code. That's evidence, not approval. Review and approve here (replying does nothing): {link}")
    if _text_admin(text):
        agents.post(case_id, "verifier", "action", "Sent the admin a signed approval link on their phone.", send_email=False)


def process(message_id: str) -> dict:
    with db.message_lock(message_id):
        return _process_locked(message_id)


def _process_locked(message_id: str) -> dict:
    try:
        reader.read(message_id)
    except Exception as e:  # fail safe: without claims nothing can be authorized; show it as held
        m = db.one("select from_address, subject from messages where id=%s", message_id)
        case_id = agents.open_case(message_id, {"from_domain": m["from_address"].split("@")[-1]}, None)
        agents.post(case_id, "auditor", "verdict",
                    f"HOLD: couldn't read this email right now ({type(e).__name__}). Nothing was paid. "
                    "It will be retried; a held email can never pay by itself.", send_email=False)
        db.one("update cases set status='held' where id=%s returning id", case_id)
        log.warning("reader failed for %s: %s", message_id, e)
        return {"decision": "hold", "reason": "reader_unavailable", "case_id": case_id}
    verdict = policy.propose_payment(message_id)
    facts = verdict["facts"]
    vendor = db.one("select name from vendors where id=%s", facts.get("vendor_id")) if facts.get("vendor_id") else None
    case_id = agents.open_case(message_id, facts, vendor["name"] if vendor else None)
    agents.post(case_id, "intake", "open", _intake_text(message_id, facts))
    agents.post(case_id, "auditor", "verdict", _auditor_text(verdict), verdict)
    _treasurer_turn(case_id, message_id, verdict)
    log.info("case %s: %s (%s)", case_id, verdict["decision"], verdict["reason"])
    return {**verdict, "case_id": case_id}
