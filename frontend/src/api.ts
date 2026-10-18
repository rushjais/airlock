export type CaseRow = {
  id: string;
  subject: string;
  status: "open" | "paid" | "held" | "denied" | "needs_approval" | "resolved";
  created_at: string;
  vendor_id: string | null;
  from_address: string;
  posts: number;
  amount_cents: number | null;
  decision: string | null;
  reason: string | null;
};

export type Post = {
  author: "intake" | "auditor" | "treasurer" | "verifier" | "admin" | "unverified" | string;
  kind: string;
  body: string;
  data: Record<string, unknown>;
  created_at: string;
  envelope?: { from: string | null; to: string | null; cc: string[]; reply_to?: string | null; subject: string;
               signed: boolean; delivered?: boolean; template?: string; why: string;
               attachments?: { name: string; label: string; note: string }[] };
};

export type Claim = { field: string; value: string; labels: string[] };
export type CaseDetail = { case: CaseRow & { source_message_id: string }; posts: Post[]; claims: Claim[] };

export type Payment = {
  id: string;
  status: string;
  amount_cents: number;
  vendor: string;
  stripe_account_id: string;
  stripe_transfer_id: string | null;
  created_at: string;
};
export type Money = { paid_cents: number; attacker_cents: number; payments: Payment[] };

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export type ModeResult = {
  emails: number; non_payable_emails: number; unauthorized_payments: number;
  emails_with_unauthorized_payment: number; cents_to_attacker: number; cents_unauthorized: number;
  legit_invoices: number; legit_paid: number;
};
export type TrickRate = { attempts: number; fooled: number; cents_to_attacker: number; runs: number; runs_with_any_loss: number };
export type Results = { available: boolean; run_id?: string; baseline?: ModeResult;
  baseline_permissive?: ModeResult; airlock?: ModeResult; trick_rate?: TrickRate };

export type Summary = { company: string; paid_cents: number; held_cents: number; stolen_cents: number };
export type CardRow = { label: string; claim: string | number | null; record: string | number | null; match: boolean;
                        money?: boolean; record_note?: string };
export type Card = { decision: string | null; reason: string | null; flags: string[]; next_action: string | null;
                     rows: CardRow[]; authorization_id?: string; case_status: string;
                     verification?: { action: string; vendor: string; from_last4: string; to_last4: string | null;
                       contact: string | null; contact_source: string; channel: string | null; asked_at: string | null;
                       answer: string; reply: string | null; transcript: string | null; bound_to: string;
                       expires_at: string | null; status: string; outcome: string } | null;
                     executed?: { status: string; paid_cents: number; paid_to_last4: string | null;
                       transfer_id: string | null; refused_last4s: string[] } | null };
export type Controls = { auto_pay_limit_cents: number; undo_window_seconds: number; approval_queue: number;
                         after_rejected_change: "pay_on_file" | "hold";
                         standing_limits: { line_item: string; monthly_cap_cents: number; used_cents: number; vendor: string }[] };

export type VendorInvoice = { number: string; po: string; amount_cents: number; status: string;
                              transfer_id: string | null; note: string | null; updated_at: string };
export type VendorBooks = { company: string; address: string; invoices: VendorInvoice[];
                            events: { at: string; invoice: string; event: string }[] };

type MEItem = { subject: string; reason: string | null; amount_cents: number | null };
export type MonthEnd = { active: boolean; running: boolean; launched: number; total: number; cases: number; open: number;
  paid: { count: number; cents: number }; stolen_cents: number; blocked: MEItem[]; held: MEItem[]; needs_you: MEItem[];
  resolved: number; digest: string | null };

export const api = {
  cases: () => get<CaseRow[]>("/api/cases"),
  case: (id: string) => get<CaseDetail>(`/api/cases/${id}`),
  money: () => get<Money>("/api/money"),
  results: () => get<Results>("/api/results"),
  summary: () => get<Summary>("/api/summary"),
  card: (id: string) => get<Card>(`/api/cases/${id}/card`),
  controls: () => get<Controls>("/api/controls"),
  setAfterRejected: (value: "pay_on_file" | "hold") =>
    fetch("/api/controls/after-rejected-change", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }) }).then((r) => r.ok),
  monthEnd: () => fetch("/api/demo/monthend", { method: "POST" }).then((r) => r.ok),
  monthEndStatus: () => get<MonthEnd>("/api/monthend"),
  corvid: () => get<VendorBooks>("/api/vendor/corvid/books"),
  reset: () => fetch("/api/demo/reset", { method: "POST" }).then((r) => r.ok),
  forgedAuditor: () => fetch("/api/demo/forged-auditor", { method: "POST" }).then((r) => r.ok),
  vendorDispute: () => fetch("/api/demo/vendor-dispute", { method: "POST" }).then((r) => r.ok),
  replay: (scenario: string) => fetch(`/api/demo/replay/${scenario}`, { method: "POST" }).then((r) => r.ok),
};

export const usd = (cents: number) =>
  (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });
