"""DEMO_MODE: visitors are isolated, the hosted demo makes no model calls, and the 'fooled model' replay never pays."""
import pytest

from airlock import config, db, demo, executor, pipeline

from .conftest import FakeStripe


@pytest.fixture
def demo_mode(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(pipeline, "execute_payment", lambda auth_id: executor.execute(auth_id))
    import airlock.reader as reader
    monkeypatch.setattr(reader, "_anthropic", lambda: pytest.fail("demo mode must not call the model"))
    demo.init()
    made: list[str] = []
    yield made
    for name in made:
        demo.drop(name)


def _in(name):
    class Ctx:
        def __enter__(self):
            self.t = db.SANDBOX.set(name)

        def __exit__(self, *a):
            db.SANDBOX.reset(self.t)
    return Ctx()


def test_each_visitor_gets_an_isolated_company(demo_mode, monkeypatch):
    FakeStripe(monkeypatch)
    a, b = demo.claim(), demo.claim()
    demo_mode += [a, b]
    assert a != b
    with _in(a):
        demo.send("legit")
        paid_a = db.one("select count(*) n from payment_authorizations where status='succeeded'")["n"]
    with _in(b):
        paid_b = db.one("select count(*) n from payment_authorizations where status='succeeded'")["n"]
        again = demo.send("legit")  # B's PO-118 is untouched by A's payment
        assert db.one("select status from cases where id=%s", again["case_id"])["status"] == "paid"
    assert paid_a == paid_b + 1


def test_demo_vendor_phone_no_pays_the_account_on_file_and_wrong_code_is_refused(demo_mode, monkeypatch):
    stripe = FakeStripe(monkeypatch)
    name = demo.claim()
    demo_mode.append(name)
    with _in(name):
        case = demo.send("bank_change")["case_id"]
        cr = demo.view(case)["out_of_band"]["change_request_id"]
        assert demo.phone_reply(cr, "yes", "000000")["reason"] == "wrong_or_expired_code"
        assert demo.phone_reply(cr, "no")["ok"]
        v = demo.view(case)
        assert v["executed"]["paid_to_last4"] == "4821" and v["executed"]["refused_last4s"] == ["9176"]
    assert stripe.calls[-1]["destination"] == "acct_1URQvLKPNEHm2UB5"


def test_fooled_model_replay_never_pays_or_reserves_anything(demo_mode, monkeypatch):
    stripe = FakeStripe(monkeypatch)
    name = demo.claim()
    demo_mode.append(name)
    with _in(name):
        case = demo.send("injection")["case_id"]
        before = len(stripe.calls)
        auths = db.one("select count(*) n from payment_authorizations")["n"]  # the seeded inbox already paid one
        out = demo.counterfactual(case)
        assert out["ok"] and out["decision"] != "pay" and out["fooled_read"]["amount_cents"] == 820000
        assert db.one("select count(*) n from payment_authorizations")["n"] == auths
        assert db.one("select status from obligations where id='ob_205'")["status"] == "accepted"
    assert len(stripe.calls) == before
