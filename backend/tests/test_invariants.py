"""One test per CLAUDE.md invariant we enforce (the list under "Tests to write before enforcement code")."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import stripe

from airlock import db, executor, ingest, policy
from airlock.replay import parse, replay

from .conftest import FakeStripe, add_claims, message_01


def test_payment_uses_registry_destination_and_obligation_amount(monkeypatch):
    fake = FakeStripe(monkeypatch)
    mid = message_01()
    d = policy.propose_payment(mid)
    assert d["decision"] == "pay"
    assert executor.execute(d["authorization_id"])["ok"]
    (call,) = fake.calls
    assert call["destination"] == db.one("select stripe_account_id from vendors where id='ven_acme'")["stripe_account_id"]
    assert call["amount"] == 145000


def test_email_supplied_account_never_reaches_stripe(monkeypatch):
    fake = FakeStripe(monkeypatch)
    mid, _ = replay("02_hacked_vendor_bank_change")
    add_claims(mid, {"vendor_name": "Acme Metals", "invoice_number": "INV-1043", "po_number": "PO-121",
                     "amount_cents": "188000", "bank_details": "account 4400019176"}, labels=("EXTERNAL_EMAIL", "MODEL_READER"))
    d = policy.propose_payment(mid)
    assert d["decision"] == "hold" and d["next_action"] == "request_vendor_verification"
    assert "authorization_id" not in d
    assert db.one("select count(*) n from payment_authorizations")["n"] == 0
    assert fake.calls == []


def test_amount_claimed_by_email_is_never_used(monkeypatch):
    mid = message_01()
    with db.tx() as conn:
        conn.execute("update claims set value='999999' where message_id=%s and field='amount_cents'", (mid,))
    d = policy.propose_payment(mid)
    assert d["decision"] == "hold" and d["reason"] == "amount_mismatch"
    assert db.one("select count(*) n from payment_authorizations")["n"] == 0


def test_executor_rejects_unknown_expired_consumed_invalidated(monkeypatch):
    FakeStripe(monkeypatch)
    assert executor.execute(str(uuid.uuid4()))["reason"] == "unknown_authorization"
    assert executor.execute("not-a-uuid")["reason"] == "unknown_authorization"

    d = policy.propose_payment(message_01())
    db.one("update payment_authorizations set expires_at=now() - interval '1 minute' where id=%s returning id",
           d["authorization_id"])
    assert executor.execute(d["authorization_id"])["reason"] == "authorization_expired"

    db.one("update payment_authorizations set expires_at=now() + interval '1 hour' where id=%s returning id",
           d["authorization_id"])
    assert executor.execute(d["authorization_id"])["ok"]
    assert executor.execute(d["authorization_id"])["reason"] == "authorization_succeeded"  # consumed

    db.one("update payment_authorizations set status='invalidated' where id=%s returning id", d["authorization_id"])
    assert executor.execute(d["authorization_id"])["reason"] == "authorization_invalidated"


def test_twenty_concurrent_executes_make_one_transfer(monkeypatch):
    fake = FakeStripe(monkeypatch, delay=0.3)
    d = policy.propose_payment(message_01())
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: executor.execute(d["authorization_id"]), range(20)))
    assert sum(r["ok"] for r in results) == 1
    assert len(fake.calls) == 1


def test_concurrent_proposals_for_one_obligation_make_one_authorization(monkeypatch):
    mids = [message_01() for _ in range(5)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        decisions = list(pool.map(policy.propose_payment, mids))
    assert sum(d["decision"] == "pay" for d in decisions) == 1
    assert db.one("select count(*) n from payment_authorizations where obligation_id='ob_118'")["n"] == 1


def test_timeout_goes_unknown_blocks_new_auth_and_reconciles_with_same_key(monkeypatch):
    fake = FakeStripe(monkeypatch, raise_exc=stripe.APIConnectionError("timeout"))
    d = policy.propose_payment(message_01())
    r = executor.execute(d["authorization_id"])
    assert r["reason"] == "unknown_outcome"
    assert db.one("select status from payment_authorizations where id=%s", d["authorization_id"])["status"] == "unknown"

    # The obligation stays reserved and no new authorization can be created for it.
    again = policy.propose_payment(message_01())
    assert again["decision"] == "deny"
    assert db.one("select count(*) n from payment_authorizations where obligation_id='ob_118'")["n"] == 1

    r2 = executor.reconcile(d["authorization_id"])
    assert r2["ok"]
    keys = {c["idempotency_key"] for c in fake.calls}
    assert len(keys) == 1, "reconcile must reuse the original idempotency key"


def test_repeated_webhook_stores_one_message():
    msg = parse("01_legit_invoice")
    first = ingest.store(msg)
    second = ingest.store(msg)
    assert first[0] == second[0] and first[1] is True and second[1] is False
    assert db.one("select count(*) n from messages where provider_message_id=%s", msg.provider_message_id)["n"] == 1


def test_registry_version_change_invalidates_pending_authorization(monkeypatch):
    fake = FakeStripe(monkeypatch)
    d = policy.propose_payment(message_01())
    db.one("update vendors set registry_version = registry_version + 1 where id='ven_acme' returning id")
    r = executor.execute(d["authorization_id"])
    assert r["reason"] == "registry_changed_invalidated"
    assert db.one("select status from payment_authorizations where id=%s", d["authorization_id"])["status"] == "invalidated"
    assert fake.calls == []


def test_undo_window_blocks_execution_until_release(monkeypatch):
    fake = FakeStripe(monkeypatch)
    d = policy.propose_payment(message_01())
    db.one("update payment_authorizations set status='scheduled', release_at=%s where id=%s returning id",
           datetime.now(timezone.utc) + timedelta(minutes=5), d["authorization_id"])
    assert executor.execute(d["authorization_id"])["reason"] == "in_undo_window"
    assert fake.calls == []
