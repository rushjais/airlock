export type CaseRow = {
  id: string;
  subject: string;
  status: "open" | "paid" | "held" | "denied" | "needs_approval";
  created_at: string;
  vendor_id: string | null;
  from_address: string;
  posts: number;
};

export type Post = {
  author: "intake" | "auditor" | "treasurer" | "verifier" | "admin" | "unverified" | string;
  kind: string;
  body: string;
  data: Record<string, unknown>;
  created_at: string;
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
export type Results = { available: boolean; run_id?: string; baseline?: ModeResult;
  baseline_permissive?: ModeResult; airlock?: ModeResult };

export const api = {
  cases: () => get<CaseRow[]>("/api/cases"),
  case: (id: string) => get<CaseDetail>(`/api/cases/${id}`),
  money: () => get<Money>("/api/money"),
  results: () => get<Results>("/api/results"),
};

export const usd = (cents: number) =>
  (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });
