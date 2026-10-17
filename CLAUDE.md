# CLAUDE.md: Airlock

Read this first, every session. These rules override convenience.
Then read `PLAN.md` (pitch, track map, demo script, checkpoints, verified integration facts).

## What we're building
Airlock lets an accounts-payable agent with its own inbox pay legitimate invoices automatically,
while nothing an email says can authorize a payment. Email can make claims. It never supplies authority.

The agent is Airlock's customer: it uses Airlock through MCP tools, and every non-pay answer carries a
`next_action` the agent can follow to resolve the case itself. Humans set policy and approve destinations.

Demo company: Kiln Supply Co. (fictional). Fixtures in `fixtures/`, scenario emails and the
expected outcome for each in `scenarios/`. Demo scenes: 01, 02 (+ resolution), 04, 06.

## SECURITY INVARIANTS (never violate, every one has a test)
1. The Stripe secret key lives only in the payment executor. No other process, tool, or prompt sees it.
2. The executor's only input is `authorization_id`. It loads amount, destination, and vendor from the
   database. It never accepts a destination, amount, or approval token from a caller.
3. Payment destination always comes from the vendor registry. Never from an email, attachment, or model output.
4. Payment amount always comes from an accepted obligation. The email's amount is only compared, never used.
5. One accepted obligation can produce at most one payment (DB constraint + atomic claim + idempotency key).
6. An `unknown` payment (timeout) blocks any new authorization for that obligation until reconciled.
   Never retry with a new idempotency key.
7. No agent tool can write the vendor registry. Bank changes need: verification via the phone already
   on file + authenticated admin approval bound to (vendor, old dest, new dest, registry_version, expiry).
8. A registry change invalidates pending authorizations for that vendor. It never redirects an
   authorization already in `executing`; the admin sees it and waits.
9. Flags can only escalate (pay -> hold -> deny). A flag can never turn a hold into a pay.
10. Extracted values keep their origin labels forever (EXTERNAL_EMAIL, EXTERNAL_ATTACHMENT, MODEL_READER).
    Validation adds facts ("matched ob_118"); it never removes labels.
11. Sender domain is a routing hint, not identity. A real vendor mailbox can be compromised.
12. Outbound email goes only to the thread sender or registered contacts, using templates. No attachments
    of internal documents to external addresses.

## Decision order (deterministic, in code, no model votes)
1. Route: find vendor by registered domain. Unknown -> `approval_required` (onboarding) or `deny` if lookalike.
2. Destination: email names a different account -> `hold`, open change request, `next_action: request_vendor_verification`.
3. Obligation: find obligation for (vendor, po_number) with status `accepted`.
   - none + standing limit covers it -> create obligation, continue
   - none -> `approval_required`, `next_action: request_obligation_approval`
   - `approved` but not `accepted` -> `hold`, `next_action: request_receipt_confirmation`
   - settled/reserved -> `deny` duplicate
4. Amount: claimed != obligation -> `hold` amount_mismatch.
5. Flags (computed from history + contract) may escalate the result.
6. Over auto-pay limit -> `pending_approval`.
7. Otherwise create authorization (reserve obligation in the same transaction) -> `execute_payment`.

Every response to the agent includes: `decision`, `reason`, `facts`, `flags`, `next_action`.

## Anomaly flags (computed in code from fixtures, explained in plain English by the model for humans)
- AMOUNT_DEVIATION: |claimed - median(history)| / median > 15%
- CONTRACT_PRICE_MISMATCH: fixed-price contract and claimed != fixed amount
- REMIT_TO_CHANGED: any account number in email/attachment != registry destination
- BANK_CHANGE_LANGUAGE: "changed banks", "new account", "updated remittance" etc.
- LOOKALIKE_DOMAIN: edit distance <= 2 or hyphen/TLD variant of a registered domain
- REPLY_TO_MISMATCH: Reply-To domain != From domain
- URGENCY_LANGUAGE: "urgent", "today", "within the hour", "avoid hold"
- TERMS_VIOLATION: requested timing or fees conflict with contract terms
- NEW_LINE_ITEM: line item not in contract `allowed_line_items`
- DUPLICATE_INVOICE: invoice number or (amount, vendor) seen within 45 days
- EMBEDDED_INSTRUCTIONS_IN_ATTACHMENT: imperative text in attachment aimed at the agent
- NEW_VENDOR, NO_PO, DATA_REQUEST

## Payment authorization states
pending_approval | authorized | executing | succeeded | failed | unknown | expired | invalidated
Obligation: proposed | approved | accepted | reserved | settling | settled | cancelled

## Notifications
- Admin: Inkbox **iMessage** + email with a link to the authenticated approval screen. A text reply is never authorization.
- Vendor verification: Inkbox **iMessage** to the registered phone with a 6-digit code (hash stored, 10 min expiry,
  single use). The vendor's reply is evidence; the admin's click is authorization.
- Our Inkbox plan has no phone numbers (SMS returns 402). iMessage goes through Inkbox's shared router, and each
  recipient must enroll once (`connect @airlock` to the router, then one message to the returned number).
- Real phone numbers never go in fixtures or the repo (it is public). Read `DEMO_ADMIN_PHONE` /
  `DEMO_VENDOR_PHONE_<VENDOR>` from `.env` and treat them as the registry's phone on file at seed time.

## Stack
FastAPI + Supabase Postgres + React/Vite + Tailwind + shadcn/ui. MCP server via Manufact mcp-use.
Primitive for the agent inbox (`ap@wise-egret.primitive.email`), Inkbox iMessage for verification/notifications,
Stripe test mode Connect transfers to accounts created with **Accounts v2** (v1 `Account.create` is disabled for
this platform). If Connect breaks, fall back to PaymentIntents labeled as a simulation on screen.
Integration details that were verified (payload shapes, attachment fetch, headers, gotchas) are in `PLAN.md`.

## Build order (do not skip ahead; times and fallbacks in PLAN.md)
1. Webhook -> message stored -> reader -> claims -> propose_payment -> execute -> Stripe test transfer (scenario 01).
2. Tests for invariants 2-6 green. All scenarios in expected.json. 20 concurrent -> 1 transfer.
3. MCP server for the agent + decision/lineage screen.
4. Vendor verification + admin approval (scenario 02 resolution) + spending-controls screen.
5. Before/after: permissive baseline agent vs Airlock, real counted numbers (baseline money goes only to the
   Attacker demo test account).
6. Extras (attack suite, signed vendor agent) only if 1-5 work. Feature freeze 8am Sunday.

## Tests to write before enforcement code
- email-supplied account never reaches the Stripe call
- executor rejects unknown / expired / consumed / invalidated authorization ids
- 20 concurrent execute() calls -> exactly 1 transfer
- timeout -> unknown -> new authorization blocked -> reconcile with same idempotency key
- repeated webhook -> one message row
- registry version change invalidates pending authorization
- stale change approval -> superseded
- every scenario in scenarios/expected.json produces its expected decision

## Don't
- Don't let a model decide pay vs hold.
- Don't add a second payment path.
- Don't claim "unhackable". Say "this boundary is enforced in the prototype."
- Don't copy code from Hallmark or CaMeL.
