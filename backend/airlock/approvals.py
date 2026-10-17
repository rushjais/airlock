"""Human approval of a destination change, designed against rubber-stamping.

Invariant 7: a bank change needs (1) the vendor's confirmation through the phone already on file and (2) an
authenticated admin approval bound to (vendor, old destination, new destination, registry_version, expiry).
Then the held invoice is SCHEDULED, not paid: an undo window with a Cancel button, and a second confirmation if
the approval was suspiciously fast while risk flags remain. The executor enforces both (release_at,
needs_reconfirm), so the UI can't skip them.

The admin is authenticated by a short-lived signed link (HMAC over the change request id + expiry). An email or
text reply is never approval.
"""
import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import agents, config, db, policy

log = logging.getLogger("airlock.approvals")
LINK_TTL = timedelta(hours=2)
FAST_APPROVAL_S = 10


def _sig(cr_id: str, exp: int) -> str:
    key = config.need("ADMIN_SESSION_SECRET").encode()
    return hmac.new(key, f"approve|{cr_id}|{exp}".encode(), hashlib.sha256).hexdigest()


def approval_link(cr_id: str) -> str:
    exp = int((datetime.now(timezone.utc) + LINK_TTL).timestamp())
    base = config.get("APP_BASE_URL", "http://localhost:5173")
    return f"{base}/approve/{cr_id}?exp={exp}&t={_sig(cr_id, exp)}"


def check(cr_id: str, exp: int, token: str) -> bool:
    return exp > time.time() and hmac.compare_digest(token or "", _sig(cr_id, exp))


def details(cr_id: str) -> dict:
    cr = db.one("select * from change_requests where id=%s", cr_id)
    if not cr:
        return {}
    vendor = db.one("select * from vendors where id=%s", cr["vendor_id"])
    ob = db.one("select * from obligations where id=%s", cr["held_obligation_id"]) if cr["held_obligation_id"] else None
    d = db.one("select flags from decisions where message_id=%s order by created_at asc limit 1", cr["source_message_id"])
    auth = db.one("""select id, status, release_at, needs_reconfirm, approved_at, stripe_transfer_id
                     from payment_authorizations where obligation_id=%s order by created_at desc limit 1""",
                  cr["held_obligation_id"]) if cr["held_obligation_id"] else None
    case = db.one("select id from cases where source_message_id=%s", cr["source_message_id"])
    return {
        "id": str(cr["id"]), "status": cr["status"], "vendor": vendor["name"],
        "old_last4": cr["old_destination_last4"], "new_last4": cr["new_last4"],
        "registry_version": vendor["registry_version"], "base_registry_version": cr["base_registry_version"],
        "evidence": cr["evidence"], "flags": d["flags"] if d else [],
        "invoice_cents": ob["amount_cents"] if ob else None, "po": ob["po_number"] if ob else None,
        "authorization": auth, "case_id": case["id"] if case else None,
        "undo_window_seconds": db.one("select undo_window_seconds from company")["undo_window_seconds"],
    }


def approve(cr_id: str, admin: str, typed_last4: str, acknowledged_flags: bool, seconds_on_page: float) -> dict:
    cr = db.one("select * from change_requests where id=%s", cr_id)
    if cr is None:
        return {"ok": False, "reason": "unknown_change_request"}
    if cr["status"] != "verified":
        return {"ok": False, "reason": f"change_request_{cr['status']}",
                "detail": "The vendor must confirm through the phone on file before an admin can approve."}
    if not cr["new_last4"] or typed_last4.strip() != cr["new_last4"]:
        return {"ok": False, "reason": "last4_mismatch", "detail": "Type the last 4 digits of the NEW account to approve."}
    flags = details(cr_id)["flags"]
    if flags and not acknowledged_flags:
        return {"ok": False, "reason": "flags_not_acknowledged"}
    company = db.one("select * from company")
    with db.tx() as conn:
        vendor = conn.execute("select * from vendors where id=%s for update", (cr["vendor_id"],)).fetchone()
        if vendor["registry_version"] != cr["base_registry_version"] or vendor["destination_last4"] != cr["old_destination_last4"]:
            conn.execute("update change_requests set status='superseded' where id=%s", (cr_id,))
            return {"ok": False, "reason": "superseded", "detail": "The registry changed since this request; it no longer applies."}
        # Registry change: new payout account on file, version bump invalidates any pending authorization (inv. 8).
        conn.execute("update vendors set destination_last4=%s, registry_version=registry_version+1 where id=%s",
                     (cr["new_last4"], vendor["id"]))
        conn.execute("""update payment_authorizations set status='invalidated'
                        where vendor_id=%s and status in ('pending_approval','authorized','scheduled')""", (vendor["id"],))
        conn.execute("""update change_requests set status='approved', evidence = evidence || %s::jsonb where id=%s""",
                     (json.dumps({"approved_by": admin, "approved_at": datetime.now(timezone.utc).isoformat(),
                                  "seconds_on_page": round(seconds_on_page, 1)}), cr_id))
    fast = seconds_on_page < FAST_APPROVAL_S and bool(flags)
    auth_id = _schedule_held_invoice(cr, admin, company["undo_window_seconds"], fast)
    case = db.one("select id from cases where source_message_id=%s", cr["source_message_id"])
    if case:
        msg = (f"Admin approved the new account ending {cr['new_last4']} (registry v{vendor['registry_version'] + 1}). "
               f"The held invoice is scheduled, not paid: {company['undo_window_seconds']}s undo window.")
        if fast:
            msg += f" Approved in {seconds_on_page:.0f}s with risk flags still present, so it won't release until the admin confirms again."
        agents.post(case["id"], "auditor", "verdict", msg)
        db.one("update cases set status='held' where id=%s returning id", case["id"])
    return {"ok": True, "authorization_id": auth_id, "needs_reconfirm": fast}


def _schedule_held_invoice(cr: dict, admin: str, window_s: int, needs_reconfirm: bool) -> str | None:
    if not cr["held_obligation_id"]:
        return None
    ob = db.one("select * from obligations where id=%s", cr["held_obligation_id"])
    vendor = db.one("select * from vendors where id=%s", cr["vendor_id"])
    inv = policy._first(policy.claims_for(str(cr["source_message_id"])), "invoice_number")
    auth_id = policy._authorize(ob, vendor, str(cr["source_message_id"]), inv, "scheduled")
    if auth_id:
        db.one("""update payment_authorizations set approved_by=%s, approved_at=now(),
                  release_at=now() + make_interval(secs => %s), needs_reconfirm=%s where id=%s returning id""",
               admin, window_s, needs_reconfirm, auth_id)
    return auth_id


def cancel(auth_id: str, admin: str) -> dict:
    with db.tx() as conn:
        a = conn.execute("""update payment_authorizations set status='invalidated', last_error=%s
                            where id=%s and status='scheduled' returning *""",
                         (f"cancelled by {admin} during undo window", auth_id)).fetchone()
        if a is None:
            return {"ok": False, "reason": "not_in_undo_window"}
        conn.execute("update obligations set status='accepted' where id=%s and status='reserved'", (a["obligation_id"],))
    return {"ok": True}


def reconfirm(auth_id: str, admin: str) -> dict:
    a = db.one("""update payment_authorizations set needs_reconfirm=false, approved_by=approved_by || %s
                  where id=%s and status='scheduled' and needs_reconfirm returning id""", f" (reconfirmed by {admin})", auth_id)
    return {"ok": bool(a)}


def _release_due() -> None:
    from . import pipeline
    due = db.all_rows("""select id, source_message_id from payment_authorizations
                         where status='scheduled' and not needs_reconfirm and release_at <= now()""")
    for a in due:
        result = pipeline.execute_payment(str(a["id"]))
        case = db.one("select id from cases where source_message_id=%s", a["source_message_id"])
        if case and result.get("ok"):
            agents.post(case["id"], "treasurer", "result",
                        f"Undo window passed with no cancel. Released ${result['amount_cents'] / 100:,.2f} to the account "
                        f"now on file. Transfer {result['transfer_id']}.", result)
            db.one("update cases set status='paid' where id=%s returning id", case["id"])


def start_release_worker(every_s: float = 2.0) -> None:
    def loop():
        while True:
            try:
                _release_due()
            except Exception:
                log.exception("release worker")
            time.sleep(every_s)
    threading.Thread(target=loop, daemon=True, name="undo-window-release").start()
