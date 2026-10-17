"""Airlock API (no Stripe key in this process).

    uv run uvicorn airlock.main:app --port 8000
"""
import json
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from email.utils import parseaddr

from . import agents, config, db, ingest, pipeline, primitive

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("airlock.api")
app = FastAPI(title="Airlock")


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
               (select count(*) from case_posts p where p.case_id=c.id) as posts
        from cases c join messages m on m.id=c.source_message_id
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


@app.get("/health")
def health():
    return {"ok": True}
