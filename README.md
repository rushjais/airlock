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

## Results
Fill in only with numbers you have actually run, with denominators, reported separately:
- Permissive baseline: __ / __ unauthorized payments executed
- Real model: __ / __ attacks where the agent proposed an unauthorized payment
- Airlock enforcement: __ / __ unauthorized payments executed; __ / __ legitimate invoices paid
- Concurrency: __ simultaneous attempts -> __ transfers
