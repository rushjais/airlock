"""Reset and seed the demo database from fixtures/.

    uv run python -m airlock.seed --reset

Demo overrides come from .env so real phones/emails never land in the public repo:
  DEMO_VENDOR_EMAIL_<ACME|BRIGHTLINE|CORVID>  registered email (its domain becomes the registered domain)
  DEMO_VENDOR_PHONE_<ACME|BRIGHTLINE|CORVID>  phone on file
"""
import json
import pathlib
import sys

import psycopg

from . import config

SHORT = {"ven_acme": "acme", "ven_brightline": "brightline", "ven_corvid": "corvid"}


def main(reset: bool, demo: bool = True) -> None:
    """demo=False ignores .env overrides (tests and the scenario oracle use pure fixtures)."""
    company = json.loads((config.FIXTURES / "company.json").read_text())
    history = json.loads((config.FIXTURES / "invoice_history.json").read_text())["history"]
    schema = (pathlib.Path(__file__).parent / "schema.sql").read_text()

    with psycopg.connect(config.need("DATABASE_URL"), autocommit=False) as conn:
        if reset:
            conn.execute(schema)
        conn.execute("set search_path to airlock")
        c = company["company"]
        conn.execute("insert into company (name, auto_pay_limit_cents) values (%s, %s)",
                     (c["name"], c["auto_pay_limit_cents"]))
        for v in company["vendors"]:
            short = SHORT[v["id"]]
            email = (demo and config.get(f"DEMO_VENDOR_EMAIL_{short.upper()}")) or v["registered_email"]
            phone = (demo and config.get(f"DEMO_VENDOR_PHONE_{short.upper()}")) or v["registered_phone"]
            conn.execute(
                """insert into vendors (id, name, registered_domain, registered_email, registered_phone,
                       inbox_alias, stripe_account_id, destination_last4, registry_version)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (v["id"], v["name"], email.split("@")[-1].lower(), email.lower(), phone,
                 f"{short}@{config.INBOX_DOMAIN}", v["stripe_account_id"], v["destination_last4"],
                 v["registry_version"]))
        for k in company["contracts"]:
            conn.execute(
                """insert into contracts (id, vendor_id, pricing, fixed_amount_cents, payment_terms_days,
                       monthly_cap_cents, allowed_line_items, late_fees_allowed)
                   values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (k["id"], k["vendor_id"], k["pricing"], k.get("fixed_amount_cents"), k["payment_terms_days"],
                 k["monthly_cap_cents"], k["allowed_line_items"], k["late_fees_allowed"]))
        for s in company["standing_limits"]:
            conn.execute("insert into standing_limits values (%s,%s,%s,%s)",
                         (s["vendor_id"], s["line_item"], s["monthly_cap_cents"], s["used_cents"]))
        for o in company["obligations"]:
            conn.execute(
                "insert into obligations (id, vendor_id, po_number, amount_cents, status, note) values (%s,%s,%s,%s,%s,%s)",
                (o["id"], o["vendor_id"], o["po_number"], o["amount_cents"], o["status"], o.get("note")))
        for h in history:
            conn.execute(
                """insert into invoice_history (vendor_id, invoice_number, invoice_date, amount_cents,
                       remit_last4, line_items) values (%s,%s,%s,%s,%s,%s)""",
                (h["vendor_id"], h["invoice"], h["date"], h["amount_cents"], h["remit_last4"], h["line_items"]))
        conn.commit()
        counts = {t: conn.execute(f"select count(*) from {t}").fetchone()[0]
                  for t in ("vendors", "contracts", "obligations", "invoice_history", "standing_limits")}
        print("seeded:", counts)
        for row in conn.execute("select id, registered_domain, inbox_alias, stripe_account_id from vendors"):
            print("  ", *row)


if __name__ == "__main__":
    main(reset="--reset" in sys.argv, demo="--fixtures-only" not in sys.argv)
