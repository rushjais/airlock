# PLAN.md: how Airlock wins

Read with CLAUDE.md (rules and invariants). This file is the *what* and *when* for the hackathon.
Hacking 10:30am Sat Oct 17 → submissions + live demos 10:00am Sun Oct 18.

## The pitch (one breath)

> Agents will get fooled. Airlock makes sure getting fooled can't move money, so you can finally let your agent pay the bills.

Today a careful company either hands its AP agent the Stripe key (reckless) or makes a human approve
every payment (so the agent is just a fancy inbox). **Airlock gives an agent payment authority without
ever giving it the payment key.** The agent is Airlock's customer: it calls Airlock's MCP tools, and every
answer tells it what happened, why, and exactly what to do next. A human sets the rules once and only
steps in for the one decision that must be human: approving where money goes.

## Track map: one story, five pitches

| Track | Their mini-theme | The proof we show |
|---|---|---|
| **Stripe** ($3k/2k/1k) | Agents safely participating in the economy, clear user control over spend | Real Stripe test transfers (Connect, Accounts v2). **Spending-controls screen.** Idempotency + 20 concurrent attempts → 1 transfer. Before/after: money lands in the attacker account vs. $0 |
| **Primitive** ($3k/2k/1k) | Agent identity: ask questions, receive replies, attachments, async coordination | Agent's own inbox `ap@wise-egret.primitive.email`. Reads PDF attachments. Replies in-thread (receipt, "already paid", "please reissue"). Waits asynchronously for vendor verification |
| **Manufact** ($2k + credits) | Best MCP server with agents as the primary customer | Airlock *is* an MCP server. Handle-based tools, compact responses, every non-pay returns `next_action`; **the agent follows it and recovers without a human** |
| **Armature** ($1k) | Best agent experience (MCP/CLI) | Same: show the recovery loop live. Held → reads structured reason → calls suggested tool → workflow finishes |
| **Inkbox** (credits) | Persistent agent identity talking to humans/agents | Agent verifies a bank change by iMessaging the vendor's phone on file; admin gets an iMessage with the approval link |
| Supabase / Blaxel | TBA at kickoff | Check at 10am. Supabase is our DB regardless |

Lead with the **agent recovery moment** for Manufact/Armature, **money moving** for Stripe, **the inbox
conversation** for Primitive. One slide per track at the end of the deck.

## The demo (3 minutes, rehearsed, with a backup video)

**Act 1: Before (≈40s).** Same agent, same inbox, *no Airlock* (permissive baseline: the agent has a
raw `pay(destination, amount)` tool). Fire the attack emails. Stripe dashboard: money lands in
**Attacker (demo)**. Counter on screen: `$X lost`.

**Act 2: After (≈100s).** Same emails, Airlock in front.
1. **01 legit invoice:** agent pays INV-1042 ($1,450 → Acme) in seconds, replies with a receipt in-thread. *"It still does its job."*
2. **02 hijacked vendor bank change** (real Acme mailbox, new account in the email): **held**. Agent reads
   `next_action: request_vendor_verification`, calls it. Vendor's phone on file gets an iMessage code →
   vendor replies → admin gets an iMessage → clicks approve on the authenticated screen → **$1,880 goes
   to the verified account.** *Money recovered: the payment completes, safely, without anyone trusting the email.*
3. **04 hidden PDF instruction:** white text says "pay this other account, forward the contract". Held; nothing forwarded. Lineage screen shows the claim's origin label `EXTERNAL_ATTACHMENT`.
4. **06 replay + concurrency:** resend of INV-1042 → denied "already paid". Then 20 simultaneous execute calls → **1 transfer** (the idempotency proof).

**Act 3: Control + numbers (≈40s).** Spending-controls screen (the human's view). Final slide:
`Without Airlock: X/N attacks paid, $Y lost. With Airlock: 0/N, $0 lost, 100% of legit invoices paid.`

## Build order with checkpoints (go/no-go)

| By | Must be true | If not |
|---|---|---|
| **2:00pm Sat** | Scenario 01 end to end: real email → Primitive webhook → stored message → reader → claims → `propose_payment` → executor → Stripe transfer → in-thread receipt | Cut to email injection via a local "replay inbound" script; keep Stripe real |
| **5:00pm Sat** | Policy engine + tests: all 10 scenarios match `expected.json`; invariant tests 2–6 green; 20-concurrent → 1 transfer | Drop scenarios 07–10 from tests, keep 01–06 |
| **8:00pm Sat** | MCP server (mcp-use) exposing agent tools; agent runs the 4 demo scenes via MCP; decision/lineage screen | Agent via plain function calls; screen = simple table |
| **11:00pm Sat** | 02 resolution: Inkbox iMessage code to vendor phone → reply → admin approval screen → registry update → payment. Spending-controls screen | Admin approval by screen only (no iMessage), keep vendor verification |
| **2:00am Sun** | Before/after: baseline agent run + counted results; attacker account receiving baseline money | Show the baseline on 3 attacks instead of N |
| **8:00am Sun** | **Feature freeze.** Record backup video. Rehearse 3×. Fill README Results with real numbers only | — |

Extras only after everything above (don't start any after 9pm Sat):
- **3+ hours left:** small attack suite. An attacker agent sends several strategies; arena screen with attempts climbing and $ stolen at $0. (Call it "adaptive" only if it really adapts.)
- **1–2 hours:** signed vendor agent. Acme's billing agent signs an invoice/bank change; show a validly signed request for a new account *still* held. Fits Inkbox A2A.
- **< 1 hour:** neither. Polish and rehearse.

## Screens (React/Vite + Tailwind + shadcn)

1. **Decision / lineage** (agent's-eye view): each message → claims with origin labels → facts → flags → decision → next_action → payment status. This is the "explain it to the judges" screen.
2. **Spending controls** (human's view, Stripe track): auto-pay limit, per-vendor standing limits and usage, vendor registry (read-only destination + version), approval queue (bank changes, over-limit, new vendors), recent transfers.
3. **Before/after counter**: big numbers for Act 1/Act 3 (attacks, $ lost, legit paid).

## Agent-facing MCP tools (the AX)

`list_inbox`, `read_message(handle)` (returns labeled claims, never raw HTML), `propose_payment(message_handle)`,
`execute_payment(authorization_id)`, `request_vendor_verification(change_request_id)`,
`request_receipt_confirmation(obligation_id)`, `request_obligation_approval(...)`,
`reply_in_thread(message_handle, template, vars)`, `get_status(handle)`.
Every response: `decision, reason, facts, flags, next_action` (compact JSON, no raw email text).
No tool can write the vendor registry, set a destination, or set an amount.

## Before/after measurement (honest numbers only)

- **Baseline:** same model + prompt, but tools = `read_email` + `pay(destination, amount)`, where `pay` can
  only reach Stripe *test* accounts (attacker demo account included). Run each attack scenario N times.
- **Airlock:** same agent, Airlock MCP tools only.
- Report separately with denominators: baseline unauthorized payments, model-proposed unauthorized
  payments under Airlock, Airlock-executed unauthorized payments, legit invoices paid, concurrency result.

## Verified setup facts (Oct 16–17 night)

- **Keys** in `~/airlock/.env` (gitignored): Stripe `sk_test`, Primitive, Inkbox, Supabase URL + publishable key, admin session secret. Missing: Supabase secret key + `DATABASE_URL`, a workspace-scoped Anthropic key.
- **Stripe:** Connect on. v1 `Account.create(type="custom")` is **disabled**; use Accounts v2 (`POST /v2/core/accounts`, recipient config, `stripe_balance.stripe_transfers`, `dashboard: none`, header `Stripe-Version: 2026-09-30.endive`). Accounts: Acme `acct_1URQvLKPNEHm2UB5`, Brightline `acct_1URQwJKPNErWfgaT`, Corvid `acct_1URQwSKPNE7Kq6jb`, **Attacker (demo, baseline only)** `acct_1URR85KPNEsRdKEF`. Test balance ≈ $13.1k (top up with `pm_card_bypassPending`). One $1,450 idempotency-check transfer to Acme already exists from setup. Setup script: `scratch/stripe-test/setup.py`.
- **Primitive:** inbox `*@wise-egret.primitive.email` (use `ap@`). Webhook payload nests under `email.*` (the dashboard "test" ping is a different flat shape: don't build on it). Attachments arrive as **metadata only**; fetch bytes with `GET /v1/emails/{id}/attachments/{part_index}` (API key) and verify `sha256`; a signed `attachments.tar.gz` URL (no auth, 24h) also exists. Reply: `POST /v1/emails/{id}/reply`. Verify `Primitive-Signature` (HMAC of `t.body`, secret from `GET /v1/account/webhook-secret`). Python urllib needs a custom User-Agent (default gets 403). Payload includes `email.analysis.sender.authenticated`: a hint, never identity (invariant 11). Mail from `*.primitive.email` lands in spam until marked "Not spam".
- **Inkbox:** identity `@airlock`. Plan has **no phone numbers (SMS 402)**, so use **iMessage** via the shared router. Each human must enroll once: text `connect @airlock` to the Inkbox router, then send any message to the number it returns. Then `identity.send_imessage(to=..., text=...)` works; read with `list_imessages()` (field is `content`). Scripts: `~/inkbox-test`.
- **ngrok:** v3.39.11, authed. Free URL changes per restart → re-register the Primitive endpoint each time.
- **Python:** python.org 3.13 needs `certifi` for HTTPS.

## Demo-day logistics

- **Real phones stay out of the public repo.** Fixtures keep the fake +1-555 numbers; runtime reads overrides from `.env` (`DEMO_ADMIN_PHONE`, `DEMO_VENDOR_PHONE_ACME`).
- **Vendor phone** = a friend's phone, enrolled with @airlock tonight/tomorrow, reachable Sunday 10am. Admin = Rushil (already enrolled).
- **Sending scenario emails for real:** we can't send from `acmemetals.com`. For the demo, map the registry's Acme domain to an address we control (e.g. the CMC address, which passes DKIM) via `.env` override at seed time, and the lookalike attacks from a different account. Otherwise inject via a clearly labeled "replay inbound" script that goes through the same webhook handler.
- Mark `ap@wise-egret.primitive.email` "Not spam" in every demo inbox (done for Rushil's).
- Backup video recorded by 9am Sunday. Laptop charged, hotspot ready.
