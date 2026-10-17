"""Bank-change approval: verified-first, bound to registry version, friction, undo window, re-confirmation."""
from datetime import timedelta

from airlock import approvals, db, executor, policy
from airlock.replay import replay

from .conftest import FakeStripe, add_claims


def _held_change() -> str:
    mid, _ = replay("02_hacked_vendor_bank_change", unique=True)
    add_claims(mid, {"vendor_name": "Acme Metals", "invoice_number": "INV-1043", "po_number": "PO-121",
                     "amount_cents": "188000"}, labels=("EXTERNAL_EMAIL", "MODEL_READER"))
    d = policy.propose_payment(mid)
    assert d["reason"] == "destination_change_requested"
    return d["facts"]["change_request_id"]


def _verify(cr_id: str) -> None:
    db.one("update change_requests set status='verified' where id=%s returning id", cr_id)


def test_admin_cannot_approve_before_vendor_verifies():
    cr = _held_change()
    r = approvals.approve(cr, "admin", "9176", True, 30)
    assert not r["ok"] and r["reason"] == "change_request_pending_verification"


def test_approval_requires_typing_new_last4():
    cr = _held_change()
    _verify(cr)
    assert approvals.approve(cr, "admin", "4821", True, 30)["reason"] == "last4_mismatch"


def test_stale_approval_is_superseded():
    cr = _held_change()
    _verify(cr)
    db.one("update vendors set registry_version=registry_version+1 where id='ven_acme' returning id")
    r = approvals.approve(cr, "admin", "9176", True, 30)
    assert r["reason"] == "superseded"
    assert db.one("select status from change_requests where id=%s", cr)["status"] == "superseded"


def test_approval_updates_registry_and_schedules_not_pays(monkeypatch):
    fake = FakeStripe(monkeypatch)
    cr = _held_change()
    _verify(cr)
    r = approvals.approve(cr, "admin", "9176", True, 30)
    assert r["ok"] and not r["needs_reconfirm"]
    v = db.one("select destination_last4, registry_version from vendors where id='ven_acme'")
    assert v["destination_last4"] == "9176" and v["registry_version"] == 2
    a = db.one("select status, release_at from payment_authorizations where id=%s", r["authorization_id"])
    assert a["status"] == "scheduled"
    assert executor.execute(r["authorization_id"])["reason"] == "in_undo_window"
    assert fake.calls == []
    # After the window, the executor releases it.
    db.one("update payment_authorizations set release_at=now() - interval '1 second' where id=%s returning id",
           r["authorization_id"])
    assert executor.execute(r["authorization_id"])["ok"]
    assert len(fake.calls) == 1


def test_fast_approval_with_flags_needs_reconfirm(monkeypatch):
    fake = FakeStripe(monkeypatch)
    cr = _held_change()
    _verify(cr)
    r = approvals.approve(cr, "admin", "9176", True, 3)
    assert r["needs_reconfirm"]
    db.one("update payment_authorizations set release_at=now() - interval '1 second' where id=%s returning id",
           r["authorization_id"])
    assert executor.execute(r["authorization_id"])["reason"] == "awaiting_admin_reconfirmation"
    assert approvals.reconfirm(r["authorization_id"], "admin")["ok"]
    assert executor.execute(r["authorization_id"])["ok"]
    assert len(fake.calls) == 1


def test_cancel_during_undo_window(monkeypatch):
    fake = FakeStripe(monkeypatch)
    cr = _held_change()
    _verify(cr)
    r = approvals.approve(cr, "admin", "9176", True, 30)
    assert approvals.cancel(r["authorization_id"], "admin")["ok"]
    db.one("update payment_authorizations set release_at=now() - interval '1 second' where id=%s returning id",
           r["authorization_id"])
    assert executor.execute(r["authorization_id"])["reason"] == "authorization_invalidated"
    assert fake.calls == []


def test_signed_link_checks():
    import time
    exp = int(time.time()) + 60
    good = approvals._sig("cr-1", exp)
    assert approvals.check("cr-1", exp, good)
    assert not approvals.check("cr-2", exp, good)
    assert not approvals.check("cr-1", exp - 120, approvals._sig("cr-1", exp - 120))  # expired
