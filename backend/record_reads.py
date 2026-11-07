"""Record the real reader's output for every demo email, once, so DEMO_MODE can replay it with no model calls.

    cd backend && uv run python record_reads.py          # needs ANTHROPIC_API_KEY and DATABASE_URL in ../.env

Writes demo/reads/<sha256 of exactly what the model saw>.json. Uses its own schema (airlock_record) and pure
fixtures, so the rendered emails are byte-identical to what a demo sandbox shows the reader.
"""
import os
import sys

os.environ["AIRLOCK_DB_SCHEMA"] = "airlock_record"
os.environ.pop("DEMO_MODE", None)

from airlock import config, reader, replay, seed  # noqa: E402

SCENARIOS = ["01_legit_invoice", "02_hacked_vendor_bank_change", "03_lookalike_domain", "04_hidden_pdf_instruction",
             "05_price_anomaly", "06_replay", "07_goods_not_received", "08_one_off_no_po", "09_exfiltration",
             "10_new_vendor_one_time", "11_subtle_combo", "12_prompt_injection_body", "13_ceo_impersonation"]

if __name__ == "__main__":
    only = sys.argv[1:] or SCENARIOS
    seed.main(reset=True, demo=False)
    for sid in only:
        mid, _ = replay.replay(sid, unique=True)
        c = reader.read(mid, record_as=sid)
        print(f"{sid:32} model={reader.MODEL} amount={c.amount_cents.value if c.amount_cents else None} "
              f"bank={[b.value for b in c.bank_details]} instructions={len(c.instructions_to_reader)}")
    print("recordings in", config.RECORDINGS)
