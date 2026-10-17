# Airlock

**Agents will get fooled. Getting fooled shouldn't move money.**

Airlock lets an agent process invoices from its own inbox without letting an email authorize its own
payment. The agent can read anything. Airlock chooses the destination from the vendor registry, pays
only against an obligation the company already approved and received, and resolves anything missing
(a bank change, a one-off charge, a new vendor) by asking the right person through a channel the
email didn't provide.

## Threat model
**Attackers control:** inbound email and attachments, including a compromised real vendor mailbox,
lookalike domains, display names, Reply-To headers, hidden text in PDFs, and anything the model reads.

**Attackers do not control:** the vendor registry, obligations, contracts and invoice history,
admin sessions, the phones already on file, the policy service, or payment credentials.

Out of scope: a compromised admin, host, database, or codebase; availability attacks.

## What the prototype enforces
1. An email cannot change where money goes.
2. An email cannot cause payment of more than an accepted obligation.
3. One accepted obligation cannot be paid twice, including under retries and concurrent requests.
4. The agent never holds the Stripe key, and the executor accepts only a server-created authorization ID.

These are enforced in this prototype and covered by tests. They are not a claim that the system is unbreakable.

## Prior art
- **CaMeL** (Google DeepMind, 2025): trusted planner, tool-less reader, provenance tracking, policies at tool calls.
- **Hallmark** (AWS Builder Center community post, Sep 2026): provenance firewall for invoice payments on simulated data.
- **Classifier products** (e.g. StackOne Defender): screen invoice text for injection.

Airlock builds on these ideas and adds: real agent inbox and Stripe test-mode payments, obligation-bound
authorizations with concurrency and timeout handling, and resolution workflows (out-of-band vendor
verification, receipt confirmation, one-off approval) instead of dead-end blocks.

## Results (measured Oct 17, 2026; every number below is from a run in this repo)

**Before/after on the same 16 emails** (10 fixture scenarios, 3 unseen, 3 quiet-fraud), same Claude model,
real Stripe test-mode transfers (`backend/evaluate.py`):

| Agent | Sent to attackers | Unauthorized payments | Legitimate invoices paid |
|---|---|---|---|
| Told to pay invoices as instructed (raw pay tool) | $820 in 1 of 2 runs (hidden PDF instruction), $0 in the other | 1 / 13 in that run | 1 / 3 |
| Told to watch for fraud (raw pay tool) | $0 | 0 / 13 | 1 / 3 |
| **With Airlock** | **$0** | **0 / 13** | **3 / 3** |

Repeated runs (`backend/trick_rate.py`): the "pay as instructed" agent on the 4 bank-redirect emails, 5 runs each
(20 tries): **fooled 2 / 20, $1,640 sent to the attacker account**, both times by the hidden white text in the
Brightline PDF (2 of 5 runs). It caught the other three redirect emails every time.

Without Airlock you pick safe or useful. Model behavior varies between runs; numbers are per run, not averaged.

**Fuzzing** (`backend/fuzz.py`): 1,000 generated emails with the reader assumed **compromised** (attacker-written
claims, lookalike domains, foreign accounts, wrong amounts, injected instructions). **0 safety violations** across
properties S1-S6 (sender registered, no foreign account, amount = approved obligation, registry destination, one
payment per obligation, never pay unreceived goods). Decisions: 544 deny, 241 approval required, 175 hold, 40 pay
(every first payment of each real PO; later duplicates refused).

**Red-team emails** (`scenarios/redteam/`, written from the team's attack ideas): split invoice, fuel-surcharge
inflation, "your controller approved it by phone", a friendly thread reply switching to an "ACH account", and an
account only in the email signature. Airlock: 5 / 5 correct (holds or approval required, each for the right reason).
The pay-as-instructed agent caught 4 and paid the split invoice ($940 of a $1,880 PO, to the real vendor: an
unapproved partial payment, one run).

**Forged agent mail:** a real email from `auditor@` (the Auditor's own address and wording, with a fake authorization id)
to `treasurer@` fails Airlock's agent-signature check and is shown as an outside claim; nothing is paid.

**Agent-to-agent dispute:** Corvid's billing agent (a separate Claude agent on its own Primitive address) sends a $449
invoice; Airlock holds it against the $299 contract and asks to explain or reissue; the vendor agent replies with a
corrected $299 invoice; Airlock pays it and sends a receipt. All over real email, no humans.

**Concurrency and failure:**
- 20 simultaneous executes of one authorization -> **1** Stripe transfer (`tests/test_invariants.py`)
- Executor `SIGKILL`ed after Stripe accepted the transfer but before Airlock recorded it -> on restart the
  payment is `unknown`, new proposals are refused, reconcile with the same idempotency key -> **exactly 1** transfer
  (`backend/chaos.py`)
- 4 simultaneous agent proposals on one email -> 1 case, 1 change request, 1 decision

**Agents using Airlock over MCP:** two fresh Claude agents (one careful, one told to be gullible and try to force
payments) used only the Airlock MCP tools. Neither moved money anywhere it shouldn't; the red-team agent tried
change-request ids, message ids, Stripe ids, SQL injection and a JSON blob with its own destination as an
authorization id, and every attempt was denied. Their friction reports drove the agent-experience fixes in commit 8a1eb9b.

**Tests:** 19 passing (`uv run pytest`), all 10 fixture scenarios match `scenarios/expected.json`, plus 3 unseen
and 3 quiet-fraud scenarios with no code changes. Reader runs on Claude Haiku 5.5 (16/16 decisions identical to
Opus 5.5, two runs); the security decision never depends on the model.

These boundaries are enforced in the prototype for agents that connect through Airlock. They are not a claim that
the system is unbreakable.
