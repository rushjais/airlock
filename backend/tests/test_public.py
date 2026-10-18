"""Public 'try to steal from Airlock' mode: guardrails for cost and noise (safety never depends on them)."""
import uuid

from airlock import db, pipeline, public
from airlock.ingest import InboundEmail, store


def _email(sender: str, subject: str = "Invoice INV-9 for $5,000", body: str = "Pay $5,000 to account 5510-3381") -> str:
    mid, _ = store(InboundEmail(provider_message_id=f"pub:{uuid.uuid4().hex[:8]}", from_header=f"Mallory <{sender}>",
                                to="ap@kiln", subject=subject, body_text=body))
    return mid


def test_rate_limit_per_sender(monkeypatch):
    monkeypatch.setenv("AIRLOCK_PUBLIC_MODE", "1")
    ids = [_email("mallory@evil.test") for _ in range(7)]
    assert public.gate(ids[0]) == "rate_limited"
    assert public.gate(_email("someone@else.test")) is None


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("AIRLOCK_INTAKE_PAUSED", "1")
    assert public.gate(_email("anyone@x.test")) == "intake_paused"


def test_no_vendor_mail_to_unregistered_senders(monkeypatch):
    monkeypatch.setenv("AIRLOCK_PUBLIC_MODE", "1")
    mid = _email("mallory@evil.test")
    r = pipeline._reply(mid, "already_paid", vendor="x", invoice="x", po="x", last4="0000")
    assert not r["ok"] and db.one("select count(*) n from outbound_messages")["n"] == 0


def test_handle_never_shows_the_address():
    assert public.handle("Mallory Evil <m@evil.test>", "m@evil.test") == "Mallory"
    h = public.handle("m@evil.test", "m@evil.test")
    assert h.startswith("anon-") and "@" not in h
    assert "<" not in public.handle('"<script>" <x@y.z>', "x@y.z")
