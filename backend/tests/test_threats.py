"""The four attacks in the hosted demo, plus the legitimate path, end to end through the real policy and executor.

Reads are the reader model's real recorded output for each email (demo/reads), so these tests make no model
calls. Stripe is a recording fake: every assertion about money is about what reached the transfer call.
"""
import json

import pytest

from airlock import approvals, config, db, executor, policy, reader, replay, verify

from .conftest import FakeStripe, add_claims, recorded_read

REGISTRY = {"ven_acme": ("acct_1URQvLKPNEHm2UB5", "4821"), "ven_brightline": ("acct_1URQwJKPNErWfgaT", "2093")}


def _email(scenario: str) -> str:
    mid, _ = replay.replay(scenario, unique=True)
    recorded_read(mid)
    return mid


def _authorizations() -> list[dict]:
    return db.all_rows("select * from payment_authorizations")


def test_legitimate_invoice_pays_the_registry_account_once(monkeypatch):
    stripe = FakeStripe(monkeypatch)
    v = policy.propose_payment(_email("01_legit_invoice"))
    assert (v["decision"], v["next_action"]) == ("pay", "execute_payment")
    assert executor.execute(v["authorization_id"])["ok"]
    assert [(c["destination"], c["amount"]) for c in stripe.calls] == [(REGISTRY["ven_acme"][0], 145000)]
    again = policy.propose_payment(_email("01_legit_invoice"))
    assert again["decision"] == "deny" and again["reason"] == "obligation_already_settled"
    assert len(stripe.calls) == 1


def test_bank_detail_change_via_email_is_held_for_out_of_band_approval(monkeypatch):
    stripe = FakeStripe(monkeypatch)
    mid = _email("02_hacked_vendor_bank_change")
    v = policy.propose_payment(mid)
    assert (v["decision"], v["reason"], v["next_action"]) == ("hold", "destination_change_requested",
                                                              "request_vendor_verification")
    assert "REMIT_TO_CHANGED" in v["flags"]
    cr = db.one("select * from change_requests where source_message_id=%s", mid)
    assert cr["status"] == "pending_verification" and cr["new_last4"] == "9176"
    assert _authorizations() == [] and stripe.calls == []
    # Approval is impossible until the vendor confirms on the phone on file: the admin screen refuses.
    out = approvals.approve(str(cr["id"]), "admin", "9176", True, 60)
    assert out == {"ok": False, "reason": "change_request_pending_verification",
                   "detail": "The vendor must confirm through the phone on file before an admin can approve."}
    assert db.one("select destination_last4 from vendors where id='ven_acme'")["destination_last4"] == "4821"


def test_vendor_denying_the_change_on_the_phone_on_file_pays_only_the_account_on_file(monkeypatch):
    stripe = FakeStripe(monkeypatch)
    mid = _email("02_hacked_vendor_bank_change")
    cr = policy.propose_payment(mid)["facts"]["change_request_id"]
    v = policy.resolve_change_rejected(cr, {"reply": "NO"})
    assert v["decision"] == "pay" and v["reason"] == "change_rejected_paying_registry_destination"
    assert executor.execute(v["authorization_id"])["ok"]
    assert [(c["destination"], c["amount"]) for c in stripe.calls] == [(REGISTRY["ven_acme"][0], 188000)]


def test_a_yes_without_the_one_time_code_from_the_phone_on_file_does_not_count(monkeypatch):
    mid = _email("02_hacked_vendor_bank_change")
    cr = policy.propose_payment(mid)["facts"]["change_request_id"]
    db.one("update change_requests set otp_hash=%s, otp_expires_at=now() + interval '10 minutes' where id=%s returning id",
           verify._hash("123456"), cr)
    assert not verify.check_code(cr, "000000")
    assert not verify.check_code(cr, "")
    assert verify.check_code(cr, "123456")


def test_spoofed_lookalike_vendor_domain_is_denied_and_never_reaches_payment(monkeypatch):
    stripe = FakeStripe(monkeypatch)
    v = policy.propose_payment(_email("03_lookalike_domain"))
    assert (v["decision"], v["reason"]) == ("deny", "sender_not_registered")
    assert "LOOKALIKE_DOMAIN:acme-metals.co~acmemetals.com" in v["flags"]
    assert v["facts"]["imitates"] == "ven_acme"
    assert _authorizations() == [] and stripe.calls == []
    assert db.one("select count(*) n from change_requests")["n"] == 0  # an outsider can't even open a bank change


def test_prompt_injection_in_invoice_body_cannot_redirect_or_inflate_the_payment(monkeypatch):
    stripe = FakeStripe(monkeypatch)
    mid = _email("12_prompt_injection_body")
    v = policy.propose_payment(mid)
    assert (v["decision"], v["reason"]) == ("hold", "destination_change_requested")
    assert "INSTRUCTIONS_TO_AGENT_IN_EMAIL" in v["flags"]
    assert _authorizations() == [] and stripe.calls == []


def test_prompt_injection_still_fails_when_the_model_is_fully_fooled(monkeypatch):
    """Replace the reader's output with exactly what the attacker asked for: their amount, their account, and no
    mention of the injected note. The decision doesn't depend on the read, so nothing pays the attacker."""
    stripe = FakeStripe(monkeypatch)
    mid, _ = replay.replay("12_prompt_injection_body", unique=True)
    add_claims(mid, {"vendor_name": "Brightline Freight", "invoice_number": "BF-89", "po_number": "PO-205",
                     "amount_cents": "820000", "bank_details": "account 7730 0412 5510, pre-verified"},
               labels=("EXTERNAL_EMAIL", "MODEL_READER"))
    v = policy.propose_payment(mid)
    assert v["decision"] != "pay" and "authorization_id" not in v
    assert _authorizations() == [] and stripe.calls == []


def test_fooled_read_without_any_account_is_held_on_the_amount_not_paid(monkeypatch):
    """Strip the attacker's account too (as if the injection only inflated the total): the approved order caps it."""
    mid, _ = replay.replay("01_legit_invoice", unique=True)
    add_claims(mid, {"vendor_name": "Acme Metals", "invoice_number": "INV-1042", "po_number": "PO-118",
                     "amount_cents": "1450000"}, labels=("EXTERNAL_EMAIL", "MODEL_READER"))
    v = policy.propose_payment(mid)
    assert (v["decision"], v["reason"]) == ("hold", "amount_mismatch")
    assert v["facts"]["obligation_cents"] == 145000


def test_urgent_ceo_impersonation_from_a_lookalike_company_domain_is_denied(monkeypatch):
    stripe = FakeStripe(monkeypatch)
    v = policy.propose_payment(_email("13_ceo_impersonation"))
    assert (v["decision"], v["reason"], v["next_action"]) == ("deny", "sender_imitates_company", "notify_admin")
    assert "COMPANY_LOOKALIKE_DOMAIN:kiln-supply.com~kilnsupply.com" in v["flags"]
    assert _authorizations() == [] and stripe.calls == []


def test_payment_request_from_a_real_executive_mailbox_is_never_auto_paid(monkeypatch):
    """A compromised exec mailbox on the real company domain: not a vendor, so it can't be paid without onboarding
    and approval, whatever it says."""
    from airlock.ingest import InboundEmail, store
    mid, _ = store(InboundEmail(provider_message_id="test:ceo-real", from_header="Dana <dana@kilnsupply.com>",
                                to="ap@kiln", subject="Wire today", body_text="Wire $24,500.00 to account 6620 0193 4471 today."))
    add_claims(mid, {"amount_cents": "2450000", "bank_details": "6620 0193 4471"}, labels=("EXTERNAL_EMAIL", "MODEL_READER"))
    v = policy.propose_payment(mid)
    assert v["decision"] == "approval_required" and _authorizations() == []


def test_email_supplied_account_never_reaches_the_transfer_call(monkeypatch):
    stripe = FakeStripe(monkeypatch)
    for s in ("01_legit_invoice", "02_hacked_vendor_bank_change", "03_lookalike_domain", "12_prompt_injection_body",
              "13_ceo_impersonation"):
        v = policy.propose_payment(_email(s))
        if v.get("authorization_id"):
            executor.execute(v["authorization_id"])
    registry = {r["stripe_account_id"] for r in db.all_rows("select stripe_account_id from vendors")}
    assert stripe.calls and all(c["destination"] in registry for c in stripe.calls)


@pytest.mark.parametrize("scenario", [s["id"] for s in json.loads(
    (config.SCENARIOS / "expected.json").read_text())["scenarios"] if s["id"] != "06_replay"])
def test_every_scenario_produces_its_expected_decision(scenario):
    exp = {s["id"]: s for s in json.loads((config.SCENARIOS / "expected.json").read_text())["scenarios"]}[scenario]
    got = policy.propose_payment(_email(scenario))
    assert got["decision"] == exp["expected_decision"]
    assert got["next_action"] == exp["next_action"]
    if "reason" in exp:
        assert got["reason"] == exp["reason"]
    assert sorted(got["flags"]) == sorted(exp["flags"])


def test_replayed_invoice_is_denied_as_a_duplicate():
    policy.propose_payment(_email("01_legit_invoice"))
    got = policy.propose_payment(_email("06_replay"))
    assert (got["decision"], got["reason"]) == ("deny", "obligation_already_settled")
    assert "DUPLICATE_INVOICE" in got["flags"]


def test_every_demo_email_has_a_recorded_read():
    for s in json.loads((config.SCENARIOS / "expected.json").read_text())["scenarios"]:
        mid, _ = replay.replay(s["id"], unique=True)
        content, _ = reader._render(mid)
        assert (config.RECORDINGS / f"{reader.recording_key(content)}.json").exists(), s["id"]
