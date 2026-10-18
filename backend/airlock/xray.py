"""Email X-ray: the inbound email with what Airlock's deterministic checks found marked in place.

Every highlight comes from the same code that decides (airlock.flags regexes, the vendor registry, Primitive's
sender verdict), so the picture can't claim more than the policy saw. Nothing here is model output.
"""
import difflib
import re

from . import db, flags as F

import json

HIDDEN = "⟦hidden character⟧"
SPAN_RULES = [  # (kind, regex, note)
    ("agent", F.AGENT_DIRECTED, "aimed at an automated agent"),
    ("bank", F.BANK_CHANGE, "bank-change story"),
    ("urgency", F.URGENCY, "pressure to skip checks"),
    ("data", F.DATA_ASK, "asks for internal documents"),
    ("agent", F.CHANNEL_AVOIDANCE, "avoids phone verification"),
    ("agent", F.SECRECY, "asks for secrecy"),
    ("urgency", F.EXEC_CLAIM, "claimed executive approval"),
    ("agent", F.PAYMENT_LINK, "link to change payment details"),
    ("agent", re.compile(re.escape(HIDDEN)), "invisible character in the email"),
]


PHONE_ON_FILE = ""


def _spans(text: str, registry_last4: str | None) -> list[dict]:
    out = []
    for kind, rx, note in SPAN_RULES:
        for m in rx.finditer(text):
            out.append({"start": m.start(), "end": m.end(), "kind": kind, "note": note})
    for m in F.ACCOUNT.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) < 4:
            continue
        same = registry_last4 and digits[-4:] == registry_last4
        out.append({"start": m.start(1), "end": m.end(1), "kind": "account_ok" if same else "account",
                    "note": "matches company records" if same else
                    f"≠ company records (••{registry_last4})" if registry_last4 else "not in company records"})
    for m in F.PHONE.finditer(text):
        num = "".join(g for g in m.groups() if g)
        same = PHONE_ON_FILE and num == PHONE_ON_FILE
        out.append({"start": m.start(), "end": m.end(), "kind": "account_ok" if same else "account",
                    "note": "phone on file" if same else "not the phone on file (never used)"})
    for m in F.ROUTING.finditer(text):
        out.append({"start": m.start(1), "end": m.end(1), "kind": "account", "note": "routing number from the email"})
    out.sort(key=lambda s: (s["start"], -s["end"]))
    merged: list[dict] = []
    for s in out:  # drop spans nested inside an earlier one; keep the outermost
        if merged and s["start"] < merged[-1]["end"]:
            continue
        merged.append(s)
    return merged


def _excerpt(text: str, last4: str | None) -> str:
    """Only the lines Airlock's checks marked (plus one line of context), so the attack is visible at a glance."""
    lines = text.split("\n")
    hit = set()
    pos = 0
    spans = _spans(text, last4)
    for i, line in enumerate(lines):
        end = pos + len(line)
        if any(sp["start"] < end and sp["end"] > pos for sp in spans):
            hit.update({i - 1, i, i + 1})
        pos = end + 1
    keep = sorted(i for i in hit if 0 <= i < len(lines))
    out, prev = [], None
    for i in keep:
        if prev is not None and i != prev + 1:
            out.append("…")
        out.append(lines[i])
        prev = i
    return "\n".join(out)


def _domain_diff(seen: str, real: str) -> list[dict]:
    """Characters of the sender's domain that differ from the registered one."""
    parts = []
    for op, i1, i2, _, _ in difflib.SequenceMatcher(None, seen, real).get_opcodes():
        parts.append({"text": seen[i1:i2], "changed": op != "equal"})
    return [p for p in parts if p["text"]]


def build(case_id: str) -> dict | None:
    c = db.one("select source_message_id from cases where id=%s", case_id)
    if not c or not c["source_message_id"]:
        return None
    mid = c["source_message_id"]
    m = db.one("select * from messages where id=%s", mid)
    raw = db.one("select body_text, attachments from message_raw where message_id=%s", mid)
    d = db.one("select facts from decisions where message_id=%s order by created_at desc limit 1", mid)
    facts = (d or {}).get("facts") or {}
    vendors = db.all_rows("select * from vendors")
    vendor = next((v for v in vendors if v["registered_domain"] == m["from_domain"]), None)
    look = None if vendor else F.lookalike(m["from_domain"], vendors)
    reg = vendor or look
    last4 = reg["destination_last4"] if reg else None
    global PHONE_ON_FILE
    PHONE_ON_FILE = re.sub(r"\D", "", (reg or {}).get("registered_phone") or "")[-10:]
    body = F.INVISIBLE.sub(HIDDEN, (raw["body_text"] or "").split("\n\n--")[0])
    atts = []
    for a in raw["attachments"] or []:
        t = F.INVISIBLE.sub(HIDDEN, (a.get("text") or "")[:4000])
        ex = _excerpt(t, last4)
        atts.append({"name": a.get("filename"), "text": t, "excerpt": ex, "excerpt_spans": _spans(ex, last4),
                     "spans": _spans(t, last4)})
    sender = {"address": m["from_address"], "domain": m["from_domain"],
              "registered": bool(vendor), "vendor": reg["name"] if reg else None,
              "authenticated": m["sender_authenticated"]}
    if look:
        sender["imitates"] = look["registered_domain"]
        if difflib.SequenceMatcher(None, m["from_domain"], look["registered_domain"]).ratio() >= 0.6:
            sender["diff"] = _domain_diff(m["from_domain"], look["registered_domain"])  # letter-level only when close
    reply_to_mismatch = bool(m["reply_to"]) and m["reply_to"].split("@")[-1].lower() != m["from_domain"]
    n_claims = db.one("select count(*) n from claims where message_id=%s and field not in ('summary','suspicion')", mid)["n"]
    first = db.one("select flags from decisions where message_id=%s order by created_at asc limit 1", mid)
    flags = (first or {}).get("flags") or []
    reviewer = []
    for r in db.all_rows("select value from claims where message_id=%s and field='suspicion'", mid):
        try:
            reviewer.append(json.loads(r["value"]))
        except ValueError:
            pass
    return {"subject": m["subject"], "subject_spans": _spans(m["subject"] or "", last4),
            "body": body, "body_spans": _spans(body, last4), "attachments": atts, "sender": sender,
            "reply_to": m["reply_to"], "reply_to_mismatch": reply_to_mismatch,
            "claims": n_claims, "authority": 0, "registry_last4": last4,
            "document_issuer": facts.get("document_issuer"), "tricks": F.explain([f for f in flags if not f.startswith("REVIEW:")]),
            "reviewer": reviewer}
