import os

os.environ["AIRLOCK_DB_SCHEMA"] = "airlock_test"  # set before airlock.config is imported
os.environ["AIRLOCK_PUBLIC_MODE"] = "0"
os.environ["AIRLOCK_INTAKE_PAUSED"] = "0"

"""Tests run against the real Supabase database (reseeded per test). Stripe is stubbed unless a test says otherwise."""
import threading
import types

import pytest

from airlock import db, seed
from airlock.replay import replay

READER_01 = {"vendor_name": "Acme Metals", "invoice_number": "INV-1042", "po_number": "PO-118",
             "amount_cents": "145000", "line_items": "Steel bar stock, 40 units"}


@pytest.fixture(autouse=True)
def fresh_db():
    seed.main(reset=True, demo=False)
    yield


def add_claims(message_id: str, claims: dict, labels=("EXTERNAL_ATTACHMENT", "MODEL_READER")):
    """Stand-in for the reader so invariant tests don't call the model."""
    with db.tx() as conn:
        for field, value in claims.items():
            conn.execute("insert into claims (message_id, field, value, labels) values (%s,%s,%s,%s)",
                         (message_id, field, value, list(labels)))


def message_01(unique=True) -> str:
    mid, _ = replay("01_legit_invoice", unique=unique)
    add_claims(mid, READER_01)
    return mid


class FakeStripe:
    """Records Transfer.create calls; optional delay widens race windows; optional failure modes."""

    def __init__(self, monkeypatch, delay=0.0, raise_exc=None):
        from airlock import executor
        self.calls: list[dict] = []
        self.lock = threading.Lock()
        self.delay = delay
        self.raise_exc = raise_exc
        self.by_key: dict[str, str] = {}
        monkeypatch.setattr(executor.stripe.Transfer, "create", self.create)

    def create(self, **kw):
        import time
        time.sleep(self.delay)
        with self.lock:
            self.calls.append(kw)
            if self.raise_exc is not None:
                exc, self.raise_exc = self.raise_exc, None
                raise exc
            tid = self.by_key.setdefault(kw["idempotency_key"], f"tr_fake_{len(self.by_key) + 1}")
        return types.SimpleNamespace(id=tid)
