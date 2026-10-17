"""Replay a fixture scenario email through the same ingest path as the Primitive webhook.

Clearly a simulation: provider ids are prefixed `replay:`. Used by tests and as the demo fallback.
    uv run python -m airlock.replay 01_legit_invoice
"""
import re
import sys
import uuid

from . import config
from .ingest import Attachment, InboundEmail, store

INVOICES = config.ROOT / "invoices"


def parse(scenario_id: str, unique: bool = False) -> InboundEmail:
    text = (config.SCENARIOS / f"{scenario_id}.md").read_text()
    head, _, body = text.partition("\n\n")
    headers: dict[str, list[str]] = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers.setdefault(k.strip().lower(), []).append(v.strip())
    attachments = []
    for a in headers.get("attachment", []):
        name = re.split(r"\s{2,}|\s\(", a)[0].strip()
        path = INVOICES / name
        if path.exists():
            attachments.append(Attachment(name, "application/pdf", path.read_bytes()))
    pid = f"replay:{scenario_id}" + (f":{uuid.uuid4().hex[:8]}" if unique else "")
    return InboundEmail(
        provider_message_id=pid,
        from_header=headers["from"][0],
        to=headers.get("to", [config.AGENT_INBOX])[0],
        subject=headers.get("subject", [None])[0],
        body_text=body.strip(),
        reply_to=headers.get("reply-to", [None])[0],
        sender_authenticated=None,
        attachments=attachments,
    )


def replay(scenario_id: str, unique: bool = False) -> tuple[str, bool]:
    return store(parse(scenario_id, unique))


if __name__ == "__main__":
    mid, created = replay(sys.argv[1], unique="--unique" in sys.argv)
    print(mid, "created" if created else "already stored")
