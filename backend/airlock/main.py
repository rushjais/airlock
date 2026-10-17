"""Airlock API (no Stripe key in this process).

    uv run uvicorn airlock.main:app --port 8000
"""
import json
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from . import db, ingest, pipeline, primitive

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
    msg = ingest.from_primitive(event)
    message_id, created = ingest.store(msg)
    if created:  # retries of the same email are acknowledged but never reprocessed
        background.add_task(_process, message_id)
    return {"ok": True, "message_id": message_id, "created": created}


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


@app.get("/health")
def health():
    return {"ok": True}
