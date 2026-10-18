"""One-way sync from Airlock's vendor registry into Inkbox, for the vendor-facing verification identity.

  - Inkbox contact card per vendor (company, phone on file, registry version), so Inkbox shows who the agent may call
    and builds its contact memory there.
  - iMessage allowlist on the verification identity: whitelist mode + one allow rule per phone on file, so even a
    fooled agent can't "verify" with a number taken from an email; the channel itself refuses.

Direction is registry -> Inkbox only. Airlock never reads the phone on file back from Inkbox: anything another
system can edit would become a way to redirect verification (invariant 7).
"""
import logging
import re

from inkbox import Inkbox

from . import config, db

log = logging.getLogger("airlock.inkbox_sync")
STATE: dict = {"contacts": {}, "allowlist": [], "whitelist": False, "error": None}


def _e164(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    return "+" + (d if len(d) > 10 else "1" + d)


def sync() -> dict:
    handle = config.get("INKBOX_VERIFY_HANDLE")
    if not handle:
        return STATE
    from inkbox.contacts.types import ContactPhone
    vendors = db.all_rows("select * from vendors order by id")
    try:
        with Inkbox(api_key=config.need("INKBOX_API_KEY")) as ib:
            existing = {}
            try:
                for c in ib.contacts.list(q="Airlock vendor", limit=50):
                    existing[(getattr(c, "company_name", "") or "").lower()] = c
            except Exception as e:
                log.info("contact list unavailable: %s", e)
            groups: dict[str, list[dict]] = {}
            for v in vendors:  # one Inkbox card per phone number (vendors may share one in the demo)
                phone = _e164(v["registered_phone"])
                if len(re.sub(r"\D", "", phone)) != 11:
                    log.info("skipping %s: phone on file isn't a dialable number", v["name"])
                    continue
                groups.setdefault(phone, []).append(v)
            for phone, vs in groups.items():
                names = " / ".join(v["name"] for v in vs)
                notes = ("Airlock vendor registry: phone on file for verification of " + ", ".join(
                    f"{v['name']} (v{v['registry_version']})" for v in vs) + ". Synced one-way from Airlock; edits here "
                    "are not used.")
                fields = dict(company_name=names, preferred_name=f"{names} (Airlock vendor)", notes=notes,
                              phones=[ContactPhone(label="on file", value=phone, is_primary=True)])
                try:
                    c = existing.get(names.lower())
                    try:
                        c = ib.contacts.update(c.id, **fields) if c else ib.contacts.create(**fields)
                        cid = str(c.id)
                    except Exception as e:  # a card for this number already exists: update that one
                        m = re.search(r"contact_id'?:\s*'([0-9a-f-]{36})", str(e))
                        if not m:
                            raise
                        cid = m.group(1)
                        ib.contacts.update(cid, **fields)
                    for v in vs:
                        STATE["contacts"][v["id"]] = {"name": names, "id": cid}
                except Exception as e:
                    log.warning("contact sync failed for %s: %s", names, e)
                try:
                    ib.imessage_contact_rules.create(handle, action="allow", match_target=phone)
                except Exception as e:
                    if "uplicate" not in str(e) and "exist" not in str(e):
                        log.warning("allow rule failed for %s: %s", names, e)
                if phone not in STATE["allowlist"]:
                    STATE["allowlist"].append(phone)
            try:
                ib.get_identity(handle).update(imessage_filter_mode="whitelist")
                STATE["whitelist"] = True
            except Exception as e:
                log.warning("whitelist mode failed: %s", e)
    except Exception as e:
        STATE["error"] = str(e)[:200]
        log.warning("inkbox sync failed: %s", e)
    log.info("inkbox sync: %d contacts, %d allowed numbers, whitelist=%s", len(STATE["contacts"]),
             len(STATE["allowlist"]), STATE["whitelist"])
    return STATE
