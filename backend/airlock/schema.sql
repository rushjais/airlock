-- Airlock schema (Postgres / Supabase). Applied by `python -m airlock.seed --reset`.
-- Derived from ../../schema.sql (design reference) with the kickoff additions:
-- per-vendor inbox aliases, quarantined raw message storage, `scheduled` authorizations (undo window).

drop schema if exists airlock cascade;
create schema airlock;
set search_path to airlock;

create table company (
  id text primary key default 'kiln',
  name text not null,
  auto_pay_limit_cents int not null,
  undo_window_seconds int not null default 60
);

create table vendors (
  id text primary key,
  name text not null,
  registered_domain text not null unique,
  registered_email text not null,
  registered_phone text not null,           -- phone on file (demo override from .env at seed time)
  inbox_alias text not null unique,         -- e.g. acme@<primitive domain>: routing hint only, never authority
  stripe_account_id text not null,          -- the only payment destination
  destination_last4 text not null,
  registry_version int not null default 1
);

create table contracts (
  id text primary key,
  vendor_id text not null references vendors(id),
  pricing text not null check (pricing in ('fixed','variable_by_order')),
  fixed_amount_cents int,
  payment_terms_days int not null,
  monthly_cap_cents int not null,
  allowed_line_items text[] not null,
  late_fees_allowed boolean not null default false
);

create table invoice_history (
  id bigserial primary key,
  vendor_id text not null references vendors(id),
  invoice_number text not null,
  invoice_date date not null,
  amount_cents int not null,
  remit_last4 text not null,
  line_items text[] not null
);

create table standing_limits (
  vendor_id text not null references vendors(id),
  line_item text not null,
  monthly_cap_cents int not null,
  used_cents int not null default 0,
  primary key (vendor_id, line_item)
);

create type obligation_status as enum
  ('proposed','approved','accepted','reserved','settling','settled','cancelled');

create table obligations (
  id text primary key,
  vendor_id text not null references vendors(id),
  po_number text,
  amount_cents int not null check (amount_cents > 0),
  status obligation_status not null default 'approved',
  source text not null default 'po',          -- po | standing_limit | one_off_approval
  note text,
  settled_invoice_number text,
  unique (vendor_id, po_number)
);

create table messages (
  id uuid primary key default gen_random_uuid(),
  provider_message_id text not null unique,   -- webhook retries dedupe here (invariant: one row per email)
  thread_id text,
  from_address text not null,
  from_domain text not null,
  to_address text,
  reply_to text,
  subject text,
  sender_authenticated boolean,               -- Primitive DMARC/DKIM verdict: a hint, never identity
  received_at timestamptz not null default now()
);

-- Quarantine: raw attacker-controlled content. Only the reader touches this; the planner never sees it.
create table message_raw (
  message_id uuid primary key references messages(id) on delete cascade,
  body_text text,
  body_html text,
  attachments jsonb not null default '[]'     -- [{filename, content_type, size_bytes, sha256, text}]
);

create table claims (                          -- what the email claims, with origin labels forever
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references messages(id) on delete cascade,
  field text not null,                         -- vendor_name, po_number, invoice_number, amount_cents, bank_account, line_items, ...
  value text not null,
  labels text[] not null                       -- {EXTERNAL_EMAIL|EXTERNAL_ATTACHMENT, MODEL_READER}
);

create type auth_status as enum ('pending_approval','authorized','scheduled','executing',
  'succeeded','failed','unknown','expired','invalidated');

create table payment_authorizations (
  id uuid primary key default gen_random_uuid(),
  obligation_id text not null references obligations(id),
  vendor_id text not null references vendors(id),
  registry_version int not null,
  stripe_account_id text not null,           -- copied from registry at creation
  amount_cents int not null,                 -- copied from obligation at creation
  source_message_id uuid not null references messages(id),
  status auth_status not null,
  idempotency_key text not null unique,
  approved_by text,
  approved_at timestamptz,
  release_at timestamptz,                    -- undo window: executor refuses before this
  expires_at timestamptz not null,
  stripe_transfer_id text,
  last_error text,
  created_at timestamptz not null default now()
);
-- Invariant 5: at most one live authorization per obligation.
create unique index one_live_auth on payment_authorizations(obligation_id)
  where status in ('pending_approval','authorized','scheduled','executing','unknown','succeeded');

create table change_requests (
  id uuid primary key default gen_random_uuid(),
  vendor_id text not null references vendors(id),
  base_registry_version int not null,
  old_destination_last4 text not null,
  new_destination text not null,             -- quarantined claim; never used for payment unless admin approves
  source_message_id uuid not null references messages(id),
  held_obligation_id text references obligations(id),
  otp_hash text,
  otp_expires_at timestamptz,
  otp_used boolean not null default false,
  status text not null default 'pending_verification', -- verified | approved | rejected | expired | superseded
  evidence jsonb not null default '{}',
  created_at timestamptz not null default now()
);

create table decisions (                       -- lineage for the UI
  id uuid primary key default gen_random_uuid(),
  message_id uuid references messages(id),
  action text not null,
  subject_id text,
  decision text not null,                      -- pay | hold | deny | approval_required | pending_approval
  reason text,
  flags text[] not null default '{}',
  facts jsonb not null,
  next_action text,
  created_at timestamptz not null default now()
);

create table outbound_messages (               -- every email/SMS/iMessage Airlock sends (invariant 12 audit)
  id uuid primary key default gen_random_uuid(),
  channel text not null,                       -- email | imessage | sms
  to_address text not null,
  template text not null,
  in_reply_to_message_id uuid references messages(id),
  provider_id text,
  body text not null,
  created_at timestamptz not null default now()
);
