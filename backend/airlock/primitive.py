"""Primitive email API client + webhook signature check. Verified shapes are documented in PLAN.md."""
import hashlib
import hmac
import time

import httpx

from . import config

API = "https://api.primitive.dev/v1"
# Primitive's edge returns 403 for default library user agents.
_HEADERS = {"User-Agent": "airlock/0.1 (+https://github.com/rushjais/airlock)"}
_secret: str | None = None


def _client() -> httpx.Client:
    return httpx.Client(base_url=API, timeout=30, headers={
        **_HEADERS, "Authorization": f"Bearer {config.need('PRIMITIVE_API_KEY')}"})


def webhook_secret() -> str:
    global _secret
    if _secret is None:
        _secret = config.get("PRIMITIVE_WEBHOOK_SECRET")
        if not _secret:
            with _client() as c:
                _secret = c.get("/account/webhook-secret").raise_for_status().json()["data"]["secret"]
    return _secret


def signature_ok(header: str, body: bytes, tolerance_s: int = 300) -> bool:
    parts = dict(p.split("=", 1) for p in (header or "").split(",") if "=" in p)
    try:
        ts = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(time.time() - ts) > tolerance_s:
        return False
    expected = hmac.new(webhook_secret().encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))


def fetch_attachment(email_id: str, part_index: int) -> bytes:
    with _client() as c:
        return c.get(f"/emails/{email_id}/attachments/{part_index}").raise_for_status().content


def reply(email_id: str, body_text: str, idempotency_key: str | None = None) -> dict:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    with _client() as c:
        r = c.post(f"/emails/{email_id}/reply", json={"body_text": body_text}, headers=headers)
        return r.raise_for_status().json()["data"]


def send(to: str, subject: str, body_text: str, from_addr: str | None = None,
         idempotency_key: str | None = None) -> dict:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    body = {"to": to, "subject": subject, "body_text": body_text, "from": from_addr or config.AGENT_INBOX}
    with _client() as c:
        return c.post("/send-mail", json=body, headers=headers).raise_for_status().json()["data"]


def list_endpoints() -> list[dict]:
    with _client() as c:
        return c.get("/endpoints").raise_for_status().json()["data"]


def create_endpoint(url: str) -> dict:
    with _client() as c:
        return c.post("/endpoints", json={"kind": "http", "url": url, "enabled": True}).raise_for_status().json()["data"]


def delete_endpoint(endpoint_id: str) -> None:
    with _client() as c:
        c.delete(f"/endpoints/{endpoint_id}").raise_for_status()
