"""Inbound email -> one `messages` row + quarantined raw content.

Both the Primitive webhook and the local scenario replay go through `store()`, so tests exercise the same path.
"""
import hashlib
import io
from dataclasses import dataclass, field
from email.utils import parseaddr

from pypdf import PdfReader

from . import db, primitive


@dataclass
class Attachment:
    filename: str | None
    content_type: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def text(self) -> str:
        """Extracted text. For PDFs this includes text a human can't see (white/tiny), on purpose:
        that's exactly what an LLM reader would ingest, so the reader must treat it as untrusted."""
        if self.content_type == "application/pdf" or (self.filename or "").lower().endswith(".pdf"):
            try:
                return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(self.data)).pages)
            except Exception as e:  # malformed PDFs are attacker-controlled too
                return f"[unreadable pdf: {e.__class__.__name__}]"
        if self.content_type.startswith("text/"):
            return self.data.decode("utf-8", "replace")
        return ""


@dataclass
class InboundEmail:
    provider_message_id: str
    from_header: str
    to: str | None
    subject: str | None
    body_text: str | None
    body_html: str | None = None
    reply_to: str | None = None
    thread_id: str | None = None
    sender_authenticated: bool | None = None
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def from_address(self) -> str:
        return parseaddr(self.from_header)[1].lower()


def from_primitive(event: dict) -> InboundEmail:
    """Build from a verified `email.received` webhook payload (nested `email.*` shape)."""
    email = event["email"]
    parsed = email["parsed"]
    attachments = [
        Attachment(a["filename"], a["content_type"], primitive.fetch_attachment(email["id"], a["part_index"]))
        for a in parsed.get("attachments") or []
    ]
    for att, meta in zip(attachments, parsed.get("attachments") or []):
        if att.sha256 != meta["sha256"]:
            raise ValueError(f"attachment {meta['filename']} failed sha256 check")
    reply_to = (parsed.get("reply_to") or [{}])[0].get("address") if parsed.get("reply_to") else None
    return InboundEmail(
        provider_message_id=email["id"],
        from_header=email["headers"]["from"],
        to=email["headers"].get("to"),
        subject=email["headers"].get("subject"),
        body_text=parsed.get("body_text"),
        body_html=parsed.get("body_html"),
        reply_to=reply_to,
        thread_id=email.get("thread_id"),
        sender_authenticated=(email.get("analysis") or {}).get("sender", {}).get("authenticated"),
        attachments=attachments,
    )


def store(msg: InboundEmail) -> tuple[str, bool]:
    """Insert once. Returns (message_id, created). Webhook retries hit the unique constraint and return created=False."""
    import json
    with db.tx() as conn:
        row = conn.execute(
            """insert into messages (provider_message_id, thread_id, from_address, from_domain, to_address,
                   reply_to, subject, sender_authenticated)
               values (%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (provider_message_id) do nothing returning id""",
            (msg.provider_message_id, msg.thread_id, msg.from_address, msg.from_address.split("@")[-1],
             msg.to, msg.reply_to, msg.subject, msg.sender_authenticated)).fetchone()
        if row is None:
            existing = conn.execute("select id from messages where provider_message_id=%s",
                                    (msg.provider_message_id,)).fetchone()
            return str(existing["id"]), False
        atts = [{"filename": a.filename, "content_type": a.content_type, "size_bytes": len(a.data),
                 "sha256": a.sha256, "text": a.text()} for a in msg.attachments]
        conn.execute("insert into message_raw (message_id, body_text, body_html, attachments) values (%s,%s,%s,%s)",
                     (row["id"], msg.body_text, msg.body_html, json.dumps(atts)))
        return str(row["id"]), True
