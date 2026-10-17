"""Deterministic decision engine (CLAUDE.md "Decision order"). No model votes.

propose_payment(message_id) -> {decision, reason, facts, flags, next_action, authorization_id?}
Destination comes only from the registry (invariant 3), amount only from the obligation (invariant 4).
"""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from . import db, flags as F
from .reader import claims_for

ESCALATE_TO_HOLD = {"EMBEDDED_INSTRUCTIONS_IN_ATTACHMENT", "REPLY_TO_MISMATCH", "BANK_CHANGE_LANGUAGE",
                    "URGENCY_LANGUAGE", "NEW_LINE_ITEM", "DATA_REQUEST", "AMOUNT_DEVIATION",
                    "CONTRACT_PRICE_MISMATCH"}
ESCALATE_TO_DENY = {"DUPLICATE_INVOICE"}
RANK = {"pay": 0, "pending_approval": 1, "approval_required": 1, "hold": 2, "deny": 3}
AUTH_TTL = timedelta(hours=1)


def _first(claims: dict, field: str) -> str | None:
    vals = claims.get(field) or []
    return vals[0]["value"] if vals else None


def _cents(v: str | None) -> int | None:
    if v is None:
        return None
    digits = re.sub(r"[^\d]", "", v)
    return int(digits) if digits else None


def _norm_po(v: str | None) -> str | None:
    if not v:
        return None
    m = re.search(r"(\d+)", v)
    return f"PO-{m.group(1)}" if m else v.strip().upper()


def _dest(v: dict) -> str:
    return f"registry:{v['id']} (..{v['destination_last4']})"


def _result(message_id, decision, reason, flags, facts, next_action, subject_id=None, authorization_id=None):
    out = {"decision": decision, "reason": reason, "flags": flags, "facts": facts, "next_action": next_action}
    if authorization_id:
        out["authorization_id"] = authorization_id
    db.one("""insert into decisions (message_id, action, subject_id, decision, reason, flags, facts, next_action)
              values (%s,'propose_payment',%s,%s,%s,%s,%s,%s) returning id""",
           message_id, subject_id, decision, reason, flags, json.dumps(facts), next_action)
    return out


def _evidence(message_id: str) -> tuple[dict, F.Evidence]:
    msg = db.one("select * from messages where id=%s", message_id)
    raw = db.one("select body_text, attachments from message_raw where message_id=%s", message_id)
    claims = claims_for(message_id)
    vendors = db.all_rows("select * from vendors")
    vendor = next((v for v in vendors if v["registered_domain"] == msg["from_domain"]), None)
    look = None if vendor else F.lookalike(msg["from_domain"], vendors)
    vid = vendor["id"] if vendor else None
    po = _norm_po(_first(claims, "po_number"))
    obligation = db.one("select * from obligations where vendor_id=%s and po_number=%s", vid, po) if vid and po else None
    ev = F.Evidence(
        from_domain=msg["from_domain"],
        reply_to=msg["reply_to"],
        subject=msg["subject"] or "",
        body=raw["body_text"] or "",
        attachment_text="\n".join(a.get("text") or "" for a in raw["attachments"] or []),
        claims=claims,
        vendor=vendor,
        lookalike_of=look,
        contract=db.one("select * from contracts where vendor_id=%s", vid) if vid else None,
        history=db.all_rows("select * from invoice_history where vendor_id=%s", vid) if vid else [],
        obligation=obligation,
        settled_invoices={r["settled_invoice_number"] for r in db.all_rows(
            "select settled_invoice_number from obligations where vendor_id=%s and settled_invoice_number is not null",
            vid)} if vid else set(),
        claimed_cents=_cents(_first(claims, "amount_cents")),
        invoice_number=_first(claims, "invoice_number"),
        other_vendors=[x for x in vendors if x["id"] != vid],
    )
    return msg, ev


def _open_change_request(message_id: str, vendor: dict, ev: F.Evidence, obligation: dict | None) -> str:
    new_dest = ", ".join(sorted(F.account_last4s(ev.body) | F.account_last4s(ev.attachment_text))) or "unspecified"
    existing = db.one("select id from change_requests where source_message_id=%s", message_id)
    if existing:
        return str(existing["id"])
    row = db.one("""insert into change_requests (vendor_id, base_registry_version, old_destination_last4,
                       new_destination, source_message_id, held_obligation_id)
                    values (%s,%s,%s,%s,%s,%s) returning id""",
                 vendor["id"], vendor["registry_version"], vendor["destination_last4"],
                 f"account(s) ending {new_dest}", message_id, obligation["id"] if obligation else None)
    others = sorted((F.account_last4s(ev.body) | F.account_last4s(ev.attachment_text)) - {vendor["destination_last4"]})
    db.one("update change_requests set new_last4=%s where id=%s returning id", others[0] if others else None, row["id"])
    return str(row["id"])


def propose_payment(message_id: str) -> dict:
    msg, ev = _evidence(message_id)
    flags = F.compute(ev)
    claimed = ev.claimed_cents
    facts: dict = {"message_id": message_id, "from_domain": ev.from_domain,
                   "claimed_cents": claimed, "invoice_number": ev.invoice_number,
                   "po_number": _norm_po(_first(ev.claims, "po_number")),
                   "sender_authenticated_hint": msg["sender_authenticated"]}

    # 1. Route by registered domain (a hint, not identity: invariant 11).
    if ev.vendor is None:
        if ev.lookalike_of is not None:
            reason = "outbound_recipient_not_registered" if "DATA_REQUEST" in flags else "sender_not_registered"
            facts["imitates"] = ev.lookalike_of["id"]
            return _result(message_id, "deny", reason, flags, facts, "notify_admin")
        return _result(message_id, "approval_required", "unknown_vendor", flags, facts, "request_vendor_onboarding")
    v = ev.vendor
    facts.update(vendor_id=v["id"], vendor_name=v["name"], registry_version=v["registry_version"], destination=_dest(v))

    # 1b. Identity consistency: the attached invoice is another vendor's document. Nothing else is evaluated
    # (no bank-change check, no phone call) until the sender sends an invoice that is actually theirs.
    other = F.issuer_mismatch(ev)
    if other is not None:
        facts["document_issuer"] = other["name"]
        return _result(message_id, "hold", "document_from_different_vendor", flags, facts,
                       "reply_in_thread:ask_vendor_to_resend")

    # 2. Destination: the email names a different account -> hold, open a change request.
    if "REMIT_TO_CHANGED" in flags:
        cr = _open_change_request(message_id, v, ev, ev.obligation)
        facts["change_request_id"] = cr
        return _result(message_id, "hold", "destination_change_requested", flags, facts,
                       "request_vendor_verification", subject_id=cr)

    # 3. Obligation.
    ob = ev.obligation
    if ob is None:
        limit = _standing_limit(v["id"], ev, claimed)
        if limit is None:
            return _result(message_id, "approval_required", "no_obligation", flags, facts, "request_obligation_approval")
        ob = _create_standing_obligation(v["id"], limit, claimed, ev.invoice_number)
        facts["standing_limit"] = limit["line_item"]
        reason_ok = f"within_standing_limit:{limit['line_item']}"
    else:
        reason_ok = "matched_accepted_obligation"
    facts.update(obligation_id=ob["id"], obligation_cents=ob["amount_cents"], obligation_status=ob["status"])
    if ob["status"] in ("approved", "proposed"):
        return _result(message_id, "hold", "obligation_not_accepted", flags, facts,
                       "request_receipt_confirmation:emp_ops_lead", subject_id=ob["id"])
    if ob["status"] in ("reserved", "settling", "settled"):
        return _result(message_id, "deny", "obligation_already_settled", flags, facts,
                       "reply_in_thread:already_paid", subject_id=ob["id"])
    if ob["status"] == "cancelled":
        return _result(message_id, "deny", "obligation_cancelled", flags, facts, "notify_admin", subject_id=ob["id"])

    # 4. Amount: the email's amount is only compared, never used.
    if claimed != ob["amount_cents"]:
        if ev.contract and ev.contract["pricing"] == "fixed":
            facts["contract_fixed_cents"] = ev.contract["fixed_amount_cents"]
        if ev.history:
            import statistics
            med = statistics.median(h["amount_cents"] for h in ev.history)
            facts["history_median_cents"] = med
            if claimed is not None:
                facts["deviation_pct"] = round(abs(claimed - med) / med * 100, 1)
        return _result(message_id, "hold", "amount_mismatch", flags, facts,
                       "reply_in_thread:ask_vendor_to_explain_or_reissue", subject_id=ob["id"])

    # 5. Flags may only escalate.
    deny = sorted(set(flags) & ESCALATE_TO_DENY)
    if deny:
        return _result(message_id, "deny", f"flag:{deny[0]}", flags, facts, "notify_admin", subject_id=ob["id"])
    hold = sorted(set(flags) & ESCALATE_TO_HOLD)
    if hold:
        return _result(message_id, "hold", f"flag:{hold[0]}", flags, facts, "notify_admin", subject_id=ob["id"])

    # 6/7. Authorization (reserves the obligation in the same transaction).
    company = db.one("select * from company")
    over = ob["amount_cents"] > company["auto_pay_limit_cents"]
    auth_id = _authorize(ob, v, message_id, ev.invoice_number, "pending_approval" if over else "authorized")
    if auth_id is None:  # lost a race to another proposal for the same obligation
        return _result(message_id, "deny", "obligation_already_reserved", flags, facts,
                       "reply_in_thread:already_paid", subject_id=ob["id"])
    facts.update(amount_cents=ob["amount_cents"], authorization_id=auth_id)
    if over:
        return _result(message_id, "pending_approval", "over_auto_pay_limit", flags, facts,
                       "await_admin_approval", subject_id=ob["id"], authorization_id=auth_id)
    return _result(message_id, "pay", reason_ok, flags, facts, "execute_payment",
                   subject_id=ob["id"], authorization_id=auth_id)


def _standing_limit(vendor_id: str, ev: F.Evidence, claimed: int | None) -> dict | None:
    if claimed is None:
        return None
    text = " ".join([ev.subject, ev.body] + [c["value"] for c in ev.claims.get("line_items", [])]).lower()
    words = set(re.findall(r"[a-z]+", text))
    for lim in db.all_rows("select * from standing_limits where vendor_id=%s", vendor_id):
        tokens = set(re.findall(r"[a-z]+", lim["line_item"].lower())) - F.GENERIC_TOKENS
        if tokens <= words and lim["used_cents"] + claimed <= lim["monthly_cap_cents"]:
            return lim
    return None


def _create_standing_obligation(vendor_id: str, limit: dict, claimed: int, invoice_number: str | None) -> dict:
    """Standing limit covers it: create an accepted obligation for exactly this invoice (idempotent per invoice)."""
    po = f"SL-{invoice_number or uuid.uuid4().hex[:6]}"
    with db.tx() as conn:
        existing = conn.execute("select * from obligations where vendor_id=%s and po_number=%s", (vendor_id, po)).fetchone()
        if existing:
            return existing
        conn.execute("update standing_limits set used_cents = used_cents + %s where vendor_id=%s and line_item=%s",
                     (claimed, vendor_id, limit["line_item"]))
        return conn.execute(
            """insert into obligations (id, vendor_id, po_number, amount_cents, status, source, note)
               values (%s,%s,%s,%s,'accepted','standing_limit',%s) returning *""",
            (f"ob_sl_{uuid.uuid4().hex[:8]}", vendor_id, po, claimed, f"standing limit: {limit['line_item']}")).fetchone()


def _authorize(ob: dict, vendor: dict, message_id: str, invoice_number: str | None, status: str) -> str | None:
    """Atomically reserve the obligation and create the one live authorization (invariant 5)."""
    auth_id = str(uuid.uuid4())
    with db.tx() as conn:
        locked = conn.execute("select status from obligations where id=%s for update", (ob["id"],)).fetchone()
        if locked["status"] != "accepted":
            return None
        blocking = conn.execute(
            "select 1 from payment_authorizations where obligation_id=%s and status='unknown'", (ob["id"],)).fetchone()
        if blocking:  # invariant 6
            return None
        conn.execute("update obligations set status='reserved', settled_invoice_number=%s where id=%s",
                     (invoice_number, ob["id"]))
        conn.execute(
            """insert into payment_authorizations (id, obligation_id, vendor_id, registry_version, stripe_account_id,
                   amount_cents, source_message_id, status, idempotency_key, expires_at)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (auth_id, ob["id"], vendor["id"], vendor["registry_version"], vendor["stripe_account_id"],
             ob["amount_cents"], message_id, status, f"airlock-auth-{auth_id}",
             datetime.now(timezone.utc) + AUTH_TTL))
    return auth_id


def reauthorize_failed(message_id: str, prior: dict) -> dict | None:
    """A 'pay' verdict whose authorization Stripe definitively rejected (status 'failed', obligation released back
    to 'accepted') gets a fresh authorization for the same obligation, vendor and amount. Anything else (unknown,
    invalidated, expired, or a registry change since) returns None and the prior verdict stands."""
    facts = prior.get("facts") or {}
    if prior["decision"] != "pay" or not facts.get("authorization_id"):
        return None
    a = db.one("select * from payment_authorizations where id=%s", facts["authorization_id"])
    if a is None or a["status"] != "failed":
        return None
    vendor = db.one("select * from vendors where id=%s", a["vendor_id"])
    if vendor["registry_version"] != a["registry_version"]:
        return None
    ob = db.one("select * from obligations where id=%s", a["obligation_id"])
    auth_id = _authorize(ob, vendor, message_id, facts.get("invoice_number"), "authorized")
    if auth_id is None:
        return None
    facts = {**facts, "authorization_id": auth_id, "replaces_authorization_id": str(a["id"]),
             "destination": _dest(vendor), "amount_cents": ob["amount_cents"]}
    return _result(message_id, "pay", prior["reason"], prior["flags"], facts, "execute_payment",
                   subject_id=ob["id"], authorization_id=auth_id)


def resolve_change_rejected(change_request_id: str, evidence: dict) -> dict:
    """The vendor told us (via the phone on file) they did NOT change banks. The change request is rejected and
    the held invoice pays the REGISTRY destination, so the attack fails and the vendor still gets paid."""
    cr = db.one("select * from change_requests where id=%s", change_request_id)
    with db.tx() as conn:
        conn.execute("update change_requests set status='rejected', evidence = evidence || %s::jsonb where id=%s",
                     (json.dumps(evidence), change_request_id))
    vendor = db.one("select * from vendors where id=%s", cr["vendor_id"])
    facts = {"change_request_id": change_request_id, "vendor_id": vendor["id"], "destination": _dest(vendor),
             "registry_version": vendor["registry_version"], "vendor_said": evidence.get("reply")}
    ob = db.one("select * from obligations where id=%s", cr["held_obligation_id"]) if cr["held_obligation_id"] else None
    if ob is None:
        return _result(cr["source_message_id"], "deny", "change_rejected_no_invoice", [], facts, "notify_admin",
                       subject_id=change_request_id)
    claimed = _cents(_first(claims_for(str(cr["source_message_id"])), "amount_cents"))
    facts.update(obligation_id=ob["id"], obligation_cents=ob["amount_cents"], claimed_cents=claimed)
    if ob["status"] != "accepted" or claimed != ob["amount_cents"]:
        return _result(cr["source_message_id"], "hold", "change_rejected_invoice_needs_review", [], facts,
                       "notify_admin", subject_id=ob["id"])
    setting = (db.one("select after_rejected_change from company") or {}).get("after_rejected_change", "pay_on_file")
    if setting == "hold":  # the admin chose to review every rejected change before anything pays
        facts.update(policy_setting="after_rejected_change=hold")
        return _result(cr["source_message_id"], "hold", "change_rejected_held_by_policy", [], facts,
                       "notify_admin", subject_id=ob["id"])
    inv = _first(claims_for(str(cr["source_message_id"])), "invoice_number")
    auth_id = _authorize(ob, vendor, str(cr["source_message_id"]), inv, "authorized")
    if auth_id is None:
        return _result(cr["source_message_id"], "deny", "obligation_already_reserved", [], facts,
                       "reply_in_thread:already_paid", subject_id=ob["id"])
    facts.update(amount_cents=ob["amount_cents"], authorization_id=auth_id)
    return _result(cr["source_message_id"], "pay", "change_rejected_paying_registry_destination", [], facts,
                   "execute_payment", subject_id=ob["id"], authorization_id=auth_id)
