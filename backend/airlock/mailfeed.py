"""Every message between every identity, in one stream: Primitive email (vendors, the agent team, Kiln's replies,
Corvid's agent) and Inkbox phone traffic (vendor checks, compromise warnings, admin briefs, admin questions).

Built from what was actually stored or sent; phone numbers are shown as last 4 digits only.
"""
import re

from . import agents, config, db

DOM = config.INBOX_DOMAIN


def _who(addr: str | None) -> dict:
    a = (addr or "").lower()
    local = a.split("@")[0]
    if a.endswith("@" + DOM):
        if local in agents.AGENTS:
            return {"label": f"{local}@", "party": "agent"}
        if local.startswith("case-"):
            return {"label": f"{local[:20]}…@" if len(local) > 20 else f"{local}@", "party": "thread"}
        if local == "corvid-billing":
            return {"label": "corvid-billing@", "party": "vendor_agent"}
        if a == config.AGENT_INBOX.lower():
            return {"label": "ap@", "party": "kiln"}
    v = db.one("select name from vendors where lower(registered_email)=%s or registered_domain=%s", a, a.split("@")[-1])
    if v:
        return {"label": a, "party": "vendor", "name": v["name"]}
    if (config.get("DEMO_ADMIN_EMAIL") or "").lower() == a:
        return {"label": "Kiln admin (email)", "party": "admin"}
    return {"label": a or "?", "party": "outside"}


def _phone(num: str) -> dict:
    digits = re.sub(r"\D", "", num or "")
    if digits and digits[-10:] == re.sub(r"\D", "", config.get("DEMO_ADMIN_PHONE") or "")[-10:]:
        admin = True
    else:
        admin = False
    v = db.one("select name from vendors where regexp_replace(registered_phone, '\\D', '', 'g') like %s",
               "%" + digits[-10:]) if digits else None
    who = v["name"] if v else ("Kiln admin" if admin else "phone")
    return {"label": f"{who} phone ••{digits[-4:]}", "party": "vendor_phone" if v else "admin"}


def feed(limit: int = 150) -> dict:
    rows: list[dict] = []
    for m in db.all_rows("""select m.id, m.from_address, m.to_address, m.subject, m.received_at, m.sender_authenticated,
                                   c.id case_id from messages m left join cases c on c.source_message_id=m.id
                            order by m.received_at desc limit %s""", limit):
        rows.append({"at": m["received_at"], "via": "Primitive", "channel": "email", "from": _who(m["from_address"]),
                     "to": [_who(m["to_address"] or config.AGENT_INBOX)], "subject": m["subject"], "case_id": m["case_id"],
                     "trust": "untrusted", "what": "inbound email: claims only"})
    for p in db.all_rows("""select p.case_id, p.author, p.kind, p.body, p.created_at, p.provider_id, c.subject
                            from case_posts p join cases c on c.id=p.case_id
                            where p.author = any(%s) and p.provider_id is not null
                            order by p.created_at desc limit %s""", list(agents.AGENTS), limit):
        rows.append({"at": p["created_at"], "via": "Primitive", "channel": "email",
                     "from": _who(agents.address(p["author"])), "to": [_who(f"{p['case_id']}@{DOM}")],
                     "cc": [_who(agents.address(a))["label"] for a in agents.AGENTS if a != p["author"]],
                     "subject": p["subject"] if p["kind"] == "open" else f"Re: {p['subject']}", "case_id": p["case_id"],
                     "trust": "signed", "what": p["body"].split("\n")[0][:140]})
    for o in db.all_rows("""select o.channel, o.to_address, o.template, o.body, o.created_at, c.id case_id
                            from outbound_messages o left join cases c on c.source_message_id=o.in_reply_to_message_id
                            order by o.created_at desc limit %s""", limit):
        phone = o["channel"] in ("sms", "imessage", "voice")
        handle = "@" + (config.get("INKBOX_VERIFY_HANDLE") or config.get("INKBOX_HANDLE") or "airlock") \
            if o["template"].startswith("vendor_") or o["template"] == "compromise_warning" else "@" + (config.get("INKBOX_HANDLE") or "airlock")
        rows.append({"at": o["created_at"], "via": "Inkbox" if phone else "Primitive", "channel": o["channel"],
                     "from": {"label": f"{handle} (Inkbox)", "party": "inkbox"} if phone else _who(config.AGENT_INBOX),
                     "to": [_phone(o["to_address"]) if phone else _who(o["to_address"])], "subject": o["template"].replace("_", " "),
                     "case_id": o["case_id"], "trust": "template", "what": (o["body"] or "").replace("\n", " ")[:160]})
    try:
        for q in db.all_rows("select asked_at, question, answer from admin_questions order by asked_at desc limit 30"):
            rows.append({"at": q["asked_at"], "via": "Inkbox", "channel": "imessage",
                         "from": {"label": "Kiln admin phone", "party": "admin"},
                         "to": [{"label": "@airlock (Inkbox)", "party": "inkbox"}], "subject": "question to Airlock",
                         "case_id": None, "trust": "read-only", "what": f"\"{q['question'][:80]}\" → {(q['answer'] or '')[:100]}"})
    except Exception:
        pass
    rows.sort(key=lambda r: r["at"], reverse=True)
    rows = rows[:limit]
    ids = {r["from"]["label"] for r in rows} | {t["label"] for r in rows for t in r["to"]}
    return {"rows": rows, "emails": sum(r["via"] == "Primitive" for r in rows),
            "inkbox": sum(r["via"] == "Inkbox" for r in rows), "identities": len(ids),
            "primitive_identities": ["ap@ (Kiln's inbox)", "intake@", "auditor@", "treasurer@", "verifier@",
                                     "case-…@ (one per case)", "corvid-billing@ (Corvid's agent)"],
            "inkbox_identities": ["@" + (config.get("INKBOX_HANDLE") or "airlock") + " (text, iMessage, voice)"]
            + (["@" + config.get("INKBOX_VERIFY_HANDLE") + " (vendor checks; iMessage allowlist: phones on file only"
                + (", whitelist on" if _sync()["whitelist"] else "") + ")"] if config.get("INKBOX_VERIFY_HANDLE") else []),
            "inkbox_contacts": sorted({c["name"] for c in _sync()["contacts"].values()})}


def _sync() -> dict:
    from . import inkbox_sync
    return inkbox_sync.STATE
