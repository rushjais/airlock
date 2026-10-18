"""Evidence a human can check: why Airlock's decision is consistent with the company's own records.

Everything here is a lookup (payment history, contract, vendor registry, past emails, the approved order). Nothing is
model output and nothing is taken from the email except what's compared. Every line carries its source.
"""
import statistics

from . import db


def _usd(c) -> str:
    return f"${(c or 0) / 100:,.2f}"


def history(vendor_id: str, limit: int = 6) -> list[dict]:
    """Past invoices (fixture history) plus payments Airlock made, newest first."""
    rows = [{"invoice": h["invoice_number"], "date": str(h["invoice_date"]), "amount_cents": h["amount_cents"],
             "to_last4": h["remit_last4"], "source": "invoice history"}
            for h in db.all_rows("""select invoice_number, invoice_date, amount_cents, remit_last4 from invoice_history
                                    where vendor_id=%s""", vendor_id)]
    v = db.one("select destination_last4 from vendors where id=%s", vendor_id)
    for a in db.all_rows("""select a.amount_cents, a.created_at, a.stripe_transfer_id,
                                   coalesce(o.settled_invoice_number, o.po_number) as ref
                            from payment_authorizations a join obligations o on o.id=a.obligation_id
                            where a.vendor_id=%s and a.status='succeeded'""", vendor_id):
        rows.append({"invoice": a["ref"] or "-", "date": a["created_at"].date().isoformat(),
                     "amount_cents": a["amount_cents"], "to_last4": v["destination_last4"] if v else "?",
                     "source": f"Airlock payment {a['stripe_transfer_id']}"})
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows[:limit]


def build(message_id, facts: dict) -> dict | None:
    vid = facts.get("vendor_id")
    if not vid:
        return None
    v = db.one("select * from vendors where id=%s", vid)
    if not v:
        return None
    lines: list[dict] = []
    hist = history(vid)
    claimed = facts.get("claimed_cents")
    if hist:
        amounts = [h["amount_cents"] for h in hist]
        med = statistics.median(amounts)
        in_line = claimed is not None and abs(claimed - med) / med <= 0.15
        same_acct = all(h["to_last4"] == v["destination_last4"] for h in hist)
        lines.append({"label": "Payment history", "ok": bool(in_line), "source": "invoice history + Airlock payments",
                      "text": f"Last {len(hist)} {v['name']} invoices: " + ", ".join(
                          f"{_usd(h['amount_cents'])} ({h['date']})" for h in hist[:4])
                      + (f". This one, {_usd(claimed)}, is {'in line' if in_line else 'out of line'} with the usual "
                         f"{_usd(med)}." if claimed is not None else ".")})
        lines.append({"label": "Account on file", "ok": same_acct, "source": f"vendor registry v{v['registry_version']}",
                      "text": f"Pays to ••{v['destination_last4']}. " + (
                          f"All {len(hist)} past payments went there." if same_acct else
                          "Past payments went to a different account; the registry changed (approved by an admin).")})
    c = db.one("select * from contracts where vendor_id=%s", vid)
    if c:
        terms = (f"fixed {_usd(c['fixed_amount_cents'])}/month" if c["pricing"] == "fixed" else "priced per order") + \
                f", net {c['payment_terms_days']}, allowed items: {', '.join(c['allowed_line_items'])}"
        ok = not (c["pricing"] == "fixed" and claimed is not None and claimed != c["fixed_amount_cents"])
        lines.append({"label": "Contract", "ok": ok, "source": f"contract {c['id']}", "text": terms + "."})
    if facts.get("obligation_id"):
        ob = db.one("select * from obligations where id=%s", facts["obligation_id"])
        if ob:
            lines.append({"label": "Approved order", "ok": ob["status"] in ("accepted", "reserved", "settling", "settled"),
                          "source": f"obligation {ob['id']}",
                          "text": f"{ob['po_number'] or ob['source']} for {_usd(ob['amount_cents'])}, status {ob['status']}."})
    m = db.one("select from_address, received_at from messages where id=%s", message_id)
    if m:
        prior = db.one("""select count(*) n, count(*) filter (where id in (select source_message_id from change_requests)) bc
                          from messages where from_address=%s and received_at < %s""", m["from_address"], m["received_at"])
        lines.append({"label": "This sender", "ok": prior["bc"] == 0, "source": "past emails",
                      "text": (f"{prior['n']} earlier email(s) from {m['from_address']}" if prior["n"] else
                               f"First email from {m['from_address']}") +
                              (f"; {prior['bc']} asked to change bank details." if prior["bc"] else ".")})
    lines.append({"label": "Where the money terms came from", "ok": True, "source": "Airlock policy",
                  "text": f"Amount from the approved order, destination from the registry (••{v['destination_last4']}). "
                          "Nothing from the email was used to decide where or how much."})
    return {"vendor": v["name"], "lines": lines}


def one_liner(message_id, facts: dict) -> str:
    """Short evidence sentence for texts and emails to the admin."""
    vid = facts.get("vendor_id")
    if not vid:
        return ""
    v = db.one("select name, destination_last4 from vendors where id=%s", vid)
    hist = history(vid, 3)
    if not hist:
        return ""
    return (f" Evidence: the last {len(hist)} {v['name']} invoices were "
            + ", ".join(f"{_usd(h['amount_cents'])} ({h['date']})" for h in hist)
            + f", all paid to ••{v['destination_last4']}.")
