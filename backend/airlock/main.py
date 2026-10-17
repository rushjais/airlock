"""Airlock API (no Stripe key in this process).

    uv run uvicorn airlock.main:app --port 8000
"""
import json
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import BaseModel

from email.utils import parseaddr

from . import agents, approvals, config, db, ingest, pipeline, policy, primitive

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("airlock.api")
app = FastAPI(title="Airlock")

from .agent_api import router as agent_router  # noqa: E402

app.include_router(agent_router)


@app.post("/webhooks/primitive")
async def primitive_webhook(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not primitive.signature_ok(request.headers.get("Primitive-Signature", ""), body):
        raise HTTPException(401, "bad signature")
    event = json.loads(body)
    kind = request.headers.get("X-Webhook-Event") or event.get("event") or event.get("type")
    if kind != "email.received" or "email" not in event:
        return {"ok": True, "ignored": kind}
    email = event["email"]
    from_addr = parseaddr(email["headers"]["from"])[1].lower()
    rcpts = [r.lower() for r in email["smtp"].get("rcpt_to") or []]
    body_text = email["parsed"].get("body_text")

    # Our own agents' signed emails echo back here: record for threading, never reprocess.
    internal = agents.verify_internal(from_addr, None, body_text)
    if internal:
        agents.record_echo(*internal, email["headers"].get("message_id"))
        return {"ok": True, "internal": internal[0]}

    msg = ingest.from_primitive(event)
    message_id, created = ingest.store(msg)
    if not created:  # retries of the same email are acknowledged but never reprocessed
        return {"ok": True, "message_id": message_id, "created": False}

    case_rcpt = next((r for r in rcpts if r.startswith("case-") and r.endswith("@" + config.INBOX_DOMAIN)), None)
    agent_rcpt = next((r for r in rcpts if r.split("@")[0] in agents.AGENTS and r.endswith("@" + config.INBOX_DOMAIN)), None)
    if case_rcpt and db.one("select 1 from cases where id=%s", case_rcpt.split("@")[0]):
        background.add_task(_case_reply, case_rcpt.split("@")[0], message_id, from_addr, body_text or "")
    else:
        if agent_rcpt:
            log.warning("unsigned email to agent address %s from %s: treating as external claims", agent_rcpt, from_addr)
        background.add_task(_process, message_id)
    return {"ok": True, "message_id": message_id, "created": True}


def _case_reply(case_id: str, message_id: str, from_addr: str, body: str) -> None:
    """Someone replied into a case thread. Admin replies are comments (yes-reply), never authorization."""
    text = body.split("\n\n--")[0].strip()[:1000]
    admin = (config.get("DEMO_ADMIN_EMAIL") or "").lower()
    if admin and from_addr == admin:
        agents.post(case_id, "admin", "admin", text, send_email=False)
        if "approve" in text.lower():
            agents.post(case_id, "auditor", "warning",
                        "Noted. An email reply can't authorize anything (anyone can send email). "
                        "Approvals only count on the authenticated admin screen.")
    else:
        agents.post(case_id, "unverified", "warning",
                    f"Unsigned message from {from_addr} posted into this case: \"{text[:300]}\". "
                    "It is not from an Airlock agent, so it's treated as an outside claim and changes nothing.",
                    send_email=False)


def _process(message_id: str) -> None:
    try:
        pipeline.process(message_id)
    except Exception:
        log.exception("processing failed for %s", message_id)


@app.get("/api/messages")
def messages(limit: int = 50):
    return db.all_rows("""
        select m.id, m.from_address, m.subject, m.received_at, d.decision, d.reason, d.flags, d.next_action, d.facts
        from messages m left join lateral (
            select * from decisions where message_id = m.id order by created_at desc limit 1) d on true
        order by m.received_at desc limit %s""", limit)


@app.get("/api/cases")
def cases(limit: int = 50):
    return db.all_rows("""
        select c.id, c.subject, c.status, c.created_at, c.vendor_id, m.from_address,
               (select count(*) from case_posts p where p.case_id=c.id) as posts,
               coalesce((d.facts->>'obligation_cents')::int, (d.facts->>'claimed_cents')::int) as amount_cents,
               d.decision, d.reason
        from cases c join messages m on m.id=c.source_message_id
        left join lateral (select facts, decision, reason from decisions where message_id=c.source_message_id
                           order by created_at desc limit 1) d on true
        order by c.created_at desc limit %s""", limit)


@app.get("/api/cases/{case_id}")
def case(case_id: str):
    c = db.one("select * from cases where id=%s", case_id)
    if not c:
        raise HTTPException(404)
    posts = db.all_rows("select author, kind, body, data, created_at from case_posts where case_id=%s order by created_at",
                        case_id)
    claims = db.all_rows("select field, value, labels from claims where message_id=%s order by field", c["source_message_id"])
    return {"case": c, "posts": posts, "claims": claims}


@app.get("/api/money")
def money():
    """Where money went (from our authorization records; Stripe is the source of truth for transfers)."""
    rows = db.all_rows("""
        select a.id, a.status, a.amount_cents, a.vendor_id, v.name as vendor, a.stripe_account_id,
               a.stripe_transfer_id, a.created_at
        from payment_authorizations a join vendors v on v.id=a.vendor_id order by a.created_at desc limit 50""")
    paid = sum(r["amount_cents"] for r in rows if r["status"] == "succeeded")
    return {"paid_cents": paid, "attacker_cents": 0, "payments": rows}


@app.get("/api/summary")
def summary():
    """Top-bar totals. 'Stolen' = money Airlock sent to any account not in the vendor registry (computed, not assumed)."""
    paid = db.one("select coalesce(sum(amount_cents),0) c from payment_authorizations where status='succeeded'")["c"]
    stolen = db.one("""select coalesce(sum(a.amount_cents),0) c from payment_authorizations a
                       where a.status='succeeded' and a.stripe_account_id not in (select stripe_account_id from vendors)""")["c"]
    held = db.one("""select coalesce(sum(coalesce((d.facts->>'obligation_cents')::int, (d.facts->>'claimed_cents')::int, 0)),0) c
                     from cases c join lateral (select facts from decisions where message_id=c.source_message_id
                                                order by created_at desc limit 1) d on true
                     where c.status in ('held','needs_approval')""")["c"]
    company = db.one("select name from company")
    return {"company": company["name"] if company else "", "paid_cents": paid, "held_cents": held, "stolen_cents": stolen}


@app.get("/api/cases/{case_id}/card")
def decision_card(case_id: str):
    """What the email claims (red) next to what the company's records say (green), and the rule that fired."""
    from . import flags as F
    c = db.one("select * from cases where id=%s", case_id)
    if not c:
        raise HTTPException(404)
    mid = c["source_message_id"]
    d = db.one("select * from decisions where message_id=%s order by created_at desc limit 1", mid)
    m = db.one("select * from messages where id=%s", mid)
    raw = db.one("select body_text, attachments from message_raw where message_id=%s", mid)
    facts = d["facts"] if d else {}
    vendor = db.one("select * from vendors where id=%s", facts.get("vendor_id")) if facts.get("vendor_id") else None
    imitated = db.one("select * from vendors where id=%s", facts.get("imitates")) if facts.get("imitates") else None
    reg = vendor or imitated
    rows = []
    rows.append({"label": "Sender", "claim": m["from_address"],
                 "record": reg["registered_email"] if reg else "no vendor with this domain",
                 "match": vendor is not None})
    text = (m["subject"] or "") + "\n" + (raw["body_text"] or "") + "\n" + "\n".join(
        a.get("text") or "" for a in raw["attachments"] or [])
    accounts = sorted(F.account_last4s(text))
    if reg:
        rows.append({"label": "Pay to", "claim": ", ".join(f"acct ••{a}" for a in accounts) if accounts else "account on file",
                     "record": f"acct ••{reg['destination_last4']}",
                     "match": all(a == reg["destination_last4"] for a in accounts)})
    if facts.get("claimed_cents") is not None or facts.get("obligation_cents") is not None:
        rows.append({"label": "Amount", "claim": facts.get("claimed_cents"), "record": facts.get("obligation_cents"),
                     "match": facts.get("claimed_cents") == facts.get("obligation_cents"), "money": True,
                     "record_note": facts.get("po_number")})
    if facts.get("obligation_status"):
        rows.append({"label": "Purchase order", "claim": facts.get("po_number"),
                     "record": f"{facts.get('po_number')} {facts['obligation_status']}",
                     "match": facts["obligation_status"] == "accepted"})
    return {"decision": d["decision"] if d else None, "reason": d["reason"] if d else None,
            "flags": d["flags"] if d else [], "next_action": d["next_action"] if d else None,
            "rows": rows, "authorization_id": facts.get("authorization_id"), "case_status": c["status"]}


@app.get("/api/controls")
def controls():
    company = db.one("select * from company")
    limits = db.all_rows("""select s.line_item, s.monthly_cap_cents, s.used_cents, v.name as vendor
                            from standing_limits s join vendors v on v.id=s.vendor_id""")
    queue = db.one("""select (select count(*) from change_requests where status in ('pending_verification','verified'))
                           + (select count(*) from payment_authorizations where status='pending_approval')
                           + (select count(*) from cases where status='needs_approval') as n""")["n"]
    return {"auto_pay_limit_cents": company["auto_pay_limit_cents"] if company else 0,
            "undo_window_seconds": company["undo_window_seconds"] if company else 0,
            "standing_limits": limits, "approval_queue": queue}


@app.post("/api/demo/reset")
def demo_reset(request: Request):
    """Restore fixtures for a clean demo. Only from this laptop: requests through the public tunnel carry
    X-Forwarded-For and are refused."""
    if request.client.host not in ("127.0.0.1", "::1") or request.headers.get("x-forwarded-for"):
        raise HTTPException(403, "reset is only available locally")
    from . import seed
    seed.main(reset=True)
    return {"ok": True}


class ApprovalAuth(BaseModel):
    exp: int
    t: str


class ApproveBody(ApprovalAuth):
    typed_last4: str
    acknowledged_flags: bool = False
    seconds_on_page: float = 0


class AuthBody(ApprovalAuth):
    authorization_id: str


def _admin(cr_id: str, body: ApprovalAuth) -> str:
    if not approvals.check(cr_id, body.exp, body.t):
        raise HTTPException(403, "approval link invalid or expired")
    return "admin (signed link)"


@app.get("/api/approvals/{cr_id}")
def approval_details(cr_id: str, exp: int, t: str):
    if not approvals.check(cr_id, exp, t):
        raise HTTPException(403, "approval link invalid or expired")
    d = approvals.details(cr_id)
    if not d:
        raise HTTPException(404)
    return d


@app.post("/api/approvals/{cr_id}/approve")
def approval_approve(cr_id: str, body: ApproveBody):
    return approvals.approve(cr_id, _admin(cr_id, body), body.typed_last4, body.acknowledged_flags, body.seconds_on_page)


@app.post("/api/approvals/{cr_id}/reject")
def approval_reject(cr_id: str, body: ApprovalAuth):
    admin = _admin(cr_id, body)
    verdict = policy.resolve_change_rejected(cr_id, {"rejected_by": admin, "reply": "rejected by admin"})
    if verdict["next_action"] == "execute_payment":
        pipeline.execute_payment(verdict["authorization_id"])
    return verdict


@app.post("/api/approvals/{cr_id}/cancel")
def approval_cancel(cr_id: str, body: AuthBody):
    return approvals.cancel(body.authorization_id, _admin(cr_id, body))


@app.post("/api/approvals/{cr_id}/reconfirm")
def approval_reconfirm(cr_id: str, body: AuthBody):
    return approvals.reconfirm(body.authorization_id, _admin(cr_id, body))


@app.on_event("startup")
def _start_workers():
    approvals.start_release_worker()
    _resume_vendor_listeners()


def _resume_vendor_listeners() -> None:
    """A restart must not orphan a verification that's waiting on the vendor's text reply."""
    import threading

    from . import agent_api
    pending = db.all_rows("""select id, source_message_id from change_requests
                             where status='pending_verification' and otp_expires_at > now()""")
    for cr in pending:
        case = db.one("select id from cases where source_message_id=%s", cr["source_message_id"])
        if case:
            target, args = pipeline._await_vendor, (case["id"], str(cr["source_message_id"]), str(cr["id"]))
        else:
            target, args = agent_api._await_vendor, (str(cr["id"]),)
        threading.Thread(target=target, args=args, daemon=True).start()
        log.info("resumed vendor-reply listener for change request %s", cr["id"])


@app.post("/api/demo/replay/{scenario:path}")
def demo_replay(scenario: str, request: Request, background: BackgroundTasks, process: bool = True):
    """Local-only: push a fixture scenario through the live pipeline (demo backup if live email misbehaves).
    process=false just lands it in the inbox, so an outside agent (e.g. Claude Desktop over MCP) handles it."""
    if request.client.host not in ("127.0.0.1", "::1") or request.headers.get("x-forwarded-for"):
        raise HTTPException(403, "replay is only available locally")
    from . import replay
    message_id, _ = replay.replay(scenario, unique=True)
    if process:
        background.add_task(_process, message_id)
    return {"ok": True, "message_id": message_id, "processed": process}


@app.get("/api/results")
def results():
    """Latest before/after measurement (written by backend/evaluate.py; survives demo resets)."""
    path = config.ROOT / "results" / "latest.json"
    if not path.exists():
        return {"available": False}
    out = {"available": True, **json.loads(path.read_text())}
    tricks = config.ROOT / "results" / "trick_rate.json"
    if tricks.exists():
        t = json.loads(tricks.read_text())
        out["trick_rate"] = {k: t[k] for k in ("attempts", "fooled", "cents_to_attacker", "runs", "runs_with_any_loss")}
    return out


@app.get("/health")
def health():
    return {"ok": True}
