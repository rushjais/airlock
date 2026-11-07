import { useCallback, useEffect, useState } from "react";

// The hosted demo. Everything here reads one private sandbox (cookie) on the server; nothing is sent anywhere.

type Claim = { field: string; value: string; labels: string[] };
type Decision = { decision: string; reason: string; rule: { step: string; text: string }; next_action: string;
                  flags?: string[]; explained?: string[] };
type View = {
  case_id: string; status: string;
  email: { from: string; reply_to: string | null; subject: string; body: string; attachments: string[] };
  asked: { amount_cents: number | null; amounts: number[]; accounts: string[] };
  reader: { model: string | null; recorded_at: string | null; claims: Claim[] };
  first_decision: Decision | null; decision: Decision;
  records: { label: string; claim: string | number | null; record: string | number | null; match: boolean; money?: boolean }[];
  executed: { status: string; paid_cents: number; paid_to_last4: string | null; transfer_id: string | null; refused_last4s: string[] } | null;
  out_of_band: { change_request_id: string; status: string; new_last4: string | null; on_file_last4: string;
                 phone_on_file_last4: string | null; approval_link: string | null; demo_passcode: string | null } | null;
  phone: { channel: string; template: string; body: string; created_at: string }[];
  admin_briefs: { body: string; created_at: string }[];
  thread: { author: string; kind: string; body: string; created_at: string }[];
};
type InboxRow = { id: string; subject: string; status: string; from_address: string; decision: string | null;
                  reason: string | null; amount_cents: number | null; created_at: string };
type Attack = { id: string; title: string; pitch: string };
type State = { inbox: InboxRow[]; attacks: Attack[]; rail: string };
type Counterfactual = { ok: boolean; reason?: string; fooled_read?: { amount_cents: number; accounts: string[] };
                        decision?: string; rule?: { step: string; text: string }; explained?: string[] };

const usd = (c: number | null | undefined) =>
  c == null ? "-" : (c / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { credentials: "same-origin", ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `Request failed (${r.status})`);
  return body as T;
}

const VERDICT: Record<string, { word: string; cls: string; ring: string }> = {
  pay: { word: "Paid", cls: "text-ok", ring: "border-ok" },
  hold: { word: "Held", cls: "text-held", ring: "border-held" },
  deny: { word: "Blocked", cls: "text-bad", ring: "border-bad" },
  approval_required: { word: "Needs approval", cls: "text-held", ring: "border-held" },
  pending_approval: { word: "Needs approval", cls: "text-held", ring: "border-held" },
};
const PILL: Record<string, string> = {
  paid: "bg-ok-soft text-ok", held: "bg-held-soft text-held", needs_approval: "bg-held-soft text-held",
  denied: "bg-bad-soft text-bad", resolved: "bg-ok-soft text-ok", open: "bg-raised text-muted",
};
const PILL_WORD: Record<string, string> = {
  paid: "Paid", held: "Held", needs_approval: "Needs approval", denied: "Blocked", resolved: "Resolved", open: "Checking",
};
const FIELD: Record<string, string> = {
  vendor_name: "Vendor", invoice_number: "Invoice", po_number: "PO", amount_cents: "Amount", bank_details: "Bank details",
  instructions_to_reader: "Instructions it noticed", line_items: "Line items", payment_terms: "Terms",
  data_requests: "Data requests", summary: "Summary",
};
const FIELD_ORDER = Object.keys(FIELD);

export default function TryIt() {
  const [state, setState] = useState<State | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => call<State>("/api/try/state").then(setState).catch((e) => setError(e.message)), []);
  useEffect(() => { refresh(); }, [refresh]);

  const loadCase = useCallback((id: string) => call<View>(`/api/try/case/${id}`).then(setView).catch(() => {}), []);
  useEffect(() => {
    if (!selected) return;
    loadCase(selected);
    const t = setInterval(() => loadCase(selected), 3000);  // picks up the undo-window release after an approval
    return () => clearInterval(t);
  }, [selected, loadCase]);

  async function send(id: string) {
    setBusy(id); setError(null);
    try {
      const out = await call<{ case_id: string; view: View }>(`/api/try/send/${id}`, { method: "POST" });
      setView(out.view); setSelected(out.case_id); refresh();
      if (window.innerWidth < 1024) {  // stacked layout: bring the verdict into view
        const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        requestAnimationFrame(() => document.getElementById("verdict")?.scrollIntoView({ behavior: reduce ? "auto" : "smooth" }));
      }
    } catch (e) { setError((e as Error).message); }
    setBusy(null);
  }

  async function reset() {
    setBusy("reset"); setError(null);
    try { await call("/api/try/reset", { method: "POST" }); setSelected(null); setView(null); await refresh(); }
    catch (e) { setError((e as Error).message); }
    setBusy(null);
  }

  return (
    <div className="min-h-full bg-bg text-text">
      <header className="mx-auto max-w-[1240px] px-4 pt-8 pb-6 sm:px-6">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h1 className="wide text-[28px] font-extrabold tracking-tight">Airlock</h1>
          <nav className="flex items-center gap-4 text-[14px] text-muted">
            <button onClick={reset} disabled={busy !== null} className="underline-offset-4 hover:text-text hover:underline disabled:opacity-50">
              {busy === "reset" ? "Resetting" : "Reset demo"}</button>
            <a href="/console" className="underline-offset-4 hover:text-text hover:underline">Full console</a>
            <a href="https://github.com/rushjais/airlock" className="underline-offset-4 hover:text-text hover:underline">Source</a>
          </nav>
        </div>
        <p className="mt-3 max-w-[62ch] text-[19px] leading-snug">
          Payment authorization for AI agents. Email can inform a payment, never redirect one.
        </p>
        <p className="mt-2 max-w-[70ch] text-[14px] leading-relaxed text-muted">
          An accounts-payable agent reads Kiln Supply Co.'s inbox and pays invoices. Send it an email below and watch
          what it is allowed to do. Fictional company and vendors; nothing is emailed or texted; payments go to a{" "}
          {state?.rail === "stripe_test" ? "Stripe test-mode account" : "simulated rail"}. Your session is private to you.
        </p>
      </header>

      <main className="mx-auto max-w-[1240px] px-4 pb-16 sm:px-6">
        <section aria-label="Send an email" className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
          {(state?.attacks ?? []).map((a) => (
            <button key={a.id} onClick={() => send(a.id)} disabled={busy !== null}
              className={`group rounded-lg border px-4 py-3 text-left transition-colors disabled:opacity-60 ${
                a.id === "legit" ? "border-ok/50 hover:bg-ok-soft" : "border-line hover:border-bad/70 hover:bg-bad-soft"}`}>
              <span className="block text-[15px] font-semibold">{busy === a.id ? "Sending" : a.title}</span>
              <span className="mt-1 block text-[13px] leading-snug text-muted">{a.pitch}</span>
            </button>
          ))}
          {!state && !error && <p className="text-muted">Setting up your private copy of the company...</p>}
        </section>
        {error && <p role="alert" className="mt-3 text-[14px] text-bad">{error}</p>}

        <div className="mt-6 grid gap-6 lg:grid-cols-[300px_1fr]">
          <Inbox rows={state?.inbox ?? []} selected={selected} onPick={setSelected} />
          {view ? <Verdict v={view} onChange={(nv) => { setView(nv); refresh(); }} /> : <Empty />}
        </div>
      </main>
    </div>
  );
}

function Empty() {
  return (
    <div className="rounded-lg border border-dashed border-line p-8 text-muted">
      <p className="text-[17px] text-text">Pick an email to send.</p>
      <p className="mt-2 max-w-[60ch] text-[14px] leading-relaxed">
        Start with the legitimate invoice, then try to steal the payment. For each email you'll see what it asked
        for, what the model read, what Airlock decided and which rule decided it, and what a human would still have to
        do out of band.
      </p>
    </div>
  );
}

function Inbox({ rows, selected, onPick }: { rows: InboxRow[]; selected: string | null; onPick: (id: string) => void }) {
  return (
    <aside aria-label="Inbox">
      <h2 className="mb-2 text-[15px] font-semibold">Inbox</h2>
      <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-panel">
        {rows.map((r) => (
          <li key={r.id}>
            <button onClick={() => onPick(r.id)} aria-current={selected === r.id}
              className={`block w-full px-3 py-2.5 text-left hover:bg-raised ${selected === r.id ? "bg-raised" : ""}`}>
              <span className="flex items-center justify-between gap-2">
                <span className="truncate text-[14px]">{r.subject.replace(/^\[[^\]]+\]\s*/, "")}</span>
                <span className={`shrink-0 rounded px-1.5 py-0.5 text-[12px] font-semibold ${PILL[r.status] ?? PILL.open}`}>
                  {PILL_WORD[r.status] ?? r.status}</span>
              </span>
              <span className="mt-0.5 flex justify-between gap-2 text-[12px] text-muted">
                <span className="truncate">{r.from_address}</span>
                <span className="num shrink-0">{usd(r.amount_cents)}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}

function Verdict({ v, onChange }: { v: View; onChange: (v: View) => void }) {
  const first = v.first_decision;
  const now = v.decision;
  const verdict = VERDICT[now.decision] ?? VERDICT.hold;
  const changed = first && (first.decision !== now.decision || first.reason !== now.reason);
  return (
    <article id="verdict" aria-label="What happened" className="min-w-0 scroll-mt-4">
      <div className="grid gap-0 overflow-hidden rounded-lg border border-line lg:grid-cols-2">
        <Outside v={v} />
        <div className="border-t-4 border-double border-line bg-panel p-5 lg:border-t-0 lg:border-l-4">
          <h2 className="text-[13px] text-muted">Airlock's decision, made in code from the company's records</h2>
          {first && changed && (
            <p className="mt-2 text-[14px] text-muted">
              First: <span className={VERDICT[first.decision]?.cls}>{VERDICT[first.decision]?.word}</span> by {first.rule.step.toLowerCase()}.
            </p>
          )}
          <p className={`wide mt-1 text-[44px] leading-none font-extrabold ${verdict.cls}`}>{verdict.word}</p>
          <p className="mt-3 text-[16px] font-semibold">{now.rule.step}</p>
          <p className="mt-1 text-[14px] leading-relaxed text-muted">{now.rule.text}</p>
          {first && first.explained && first.explained.length > 0 && (
            <div className="mt-4">
              <h3 className="text-[13px] text-muted">Also noticed (these can only make it stricter)</h3>
              <ul className="mt-1 list-disc pl-5 text-[14px] leading-relaxed">
                {first.explained.map((e) => <li key={e}>{e}</li>)}
              </ul>
            </div>
          )}
          <Records rows={v.records} />
          <OutOfBand v={v} onChange={onChange} />
          <Moved v={v} />
        </div>
      </div>
      <Thread v={v} />
    </article>
  );
}

function Outside({ v }: { v: View }) {
  const claims = [...v.reader.claims].sort((a, b) => FIELD_ORDER.indexOf(a.field) - FIELD_ORDER.indexOf(b.field))
    .filter((c) => c.field !== "suspicion");
  const asked = v.asked.accounts.length ? `account ending ${v.asked.accounts.join(", ")}` : "the account on file";
  const [cf, setCf] = useState<Counterfactual | null>(null);
  const [cfBusy, setCfBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => { setCf(null); setShowAll(false); }, [v.case_id]);
  const noticed = v.reader.claims.filter((c) => c.field === "instructions_to_reader").length;
  const canFool = v.first_decision && v.first_decision.decision !== "pay" && /\$[\d,]+\.\d{2}/.test(v.email.body);
  return (
    <div className="bg-bg p-5">
      <h2 className="text-[13px] text-muted">The email, from outside the company (untrusted)</h2>
      <div className="mt-2 rounded-md border border-line bg-raised p-4">
        <p className="text-[13px] text-muted">From <span className="text-text">{v.email.from}</span>
          {v.email.reply_to && <> , replies to <span className="text-bad">{v.email.reply_to}</span></>}</p>
        <p className="mt-1 text-[15px] font-semibold">{v.email.subject}</p>
        <div className="font-letter mt-3 max-h-72 overflow-auto text-[16px] leading-[1.55] whitespace-pre-wrap">{reflow(v.email.body)}</div>
        {v.email.attachments.length > 0 && (
          <p className="mt-2 text-[13px] text-muted">Attached: {v.email.attachments.join(", ")}</p>)}
      </div>
      <p className="mt-4 text-[15px] leading-relaxed">
        The agent is asked to pay <span className="num font-semibold">{usd(v.asked.amount_cents)}</span> to {asked}.
        {(v.asked.amounts ?? []).length > 1 && <span className="text-muted"> (The email names {v.asked.amounts.map(usd).join(" and ")}.)</span>}
      </p>

      <h3 className="mt-5 text-[15px] font-semibold">What the model read</h3>
      <p className="mt-1 border-l-2 border-held pl-3 text-[14px] leading-relaxed">
        The model's read can be wrong; the payment decision never depends on it.
      </p>
      {noticed > 0 && (
        <p className="mt-2 text-[14px] leading-relaxed text-muted">This time the model caught it and reported
          {noticed === 1 ? " one instruction" : ` ${noticed} instructions`} aimed at the agent. Policy doesn't rely on that either.</p>)}
      <p className="mt-2 text-[12px] text-muted">
        {v.reader.model ? `Real ${v.reader.model} output for this exact email, recorded ${v.reader.recorded_at?.slice(0, 10)} and replayed here (no model calls in the demo). ` : ""}
        Every value keeps its origin label.</p>
      <dl className="mt-2 divide-y divide-line rounded-md border border-line text-[13px]">
        {(showAll ? claims : claims.slice(0, 6)).map((c, i) => (
          <div key={i} className="grid grid-cols-[104px_1fr] gap-2 px-3 py-1.5">
            <dt className="text-muted">{FIELD[c.field] ?? c.field}</dt>
            <dd className="min-w-0 break-words">
              {c.field === "amount_cents" ? usd(Number(c.value)) : c.value}
              <span className="ml-2 text-[11px] text-muted">{c.labels.map(labelWord).join(", ")}</span>
            </dd>
          </div>
        ))}
        {claims.length > 6 && (
          <button onClick={() => setShowAll(!showAll)} className="block w-full px-3 py-1.5 text-left text-[13px] text-muted hover:text-text">
            {showAll ? "Show fewer" : `Show all ${claims.length} values`}</button>)}
      </dl>
      {canFool && !cf && (
        <button onClick={async () => { setCfBusy(true); try { setCf(await call(`/api/try/counterfactual/${v.case_id}`, { method: "POST" })); } finally { setCfBusy(false); } }}
          disabled={cfBusy} className="mt-3 rounded-md border border-line px-3 py-1.5 text-[14px] hover:bg-raised disabled:opacity-60">
          {cfBusy ? "Re-running the policy" : "What if the model had been fooled?"}
        </button>
      )}
      {cf && cf.ok && cf.fooled_read && (
        <div className="mt-3 rounded-md border border-line bg-panel p-3 text-[14px] leading-relaxed">
          <p>We replaced the model's read with what the attacker wanted it to say: {usd(cf.fooled_read.amount_cents)}
            {cf.fooled_read.accounts.length > 0 && <> to account ending {cf.fooled_read.accounts.join(", ")}</>}, no warnings.
            The same policy then decided: <span className={`font-semibold ${VERDICT[cf.decision!]?.cls}`}>{VERDICT[cf.decision!]?.word}</span>, by {cf.rule?.step.toLowerCase()}.</p>
          <p className="mt-1 text-muted">Nothing was paid. Where money goes comes from the vendor registry and how much from
            the approved order, so a fooled read can't change either.</p>
        </div>
      )}
      {cf && !cf.ok && <p className="mt-3 text-[13px] text-muted">There's no amount in this email for a fooled read to change.</p>}
    </div>
  );
}

function labelWord(l: string) {
  return { EXTERNAL_EMAIL: "email", EXTERNAL_ATTACHMENT: "attachment", MODEL_READER: "model" }[l] ?? l;
}

// Scenario emails are hard-wrapped; join wrapped lines so the letter reflows to the panel width.
function reflow(body: string) {
  return body.replace(/([^\n])\n(?!\n)/g, "$1 ");
}

function Records({ rows }: { rows: View["records"] }) {
  if (!rows.length) return null;
  return (
    <div className="mt-5">
      <h3 className="text-[13px] text-muted">The email's claims against the records</h3>
      <table className="mt-1 w-full text-left text-[13px]">
        <thead className="text-muted"><tr><th className="py-1 font-normal"></th><th className="font-normal">Email says</th><th className="font-normal">Records say</th></tr></thead>
        <tbody className="divide-y divide-line">
          {rows.map((r) => (
            <tr key={r.label}>
              <td className="py-1.5 pr-2 text-muted">{r.label}</td>
              <td className={`pr-2 ${r.match ? "" : "text-bad"}`}>{r.money ? usd(r.claim as number) : r.claim ?? "-"}</td>
              <td className="text-ok">{r.money ? usd(r.record as number) : r.record ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OutOfBand({ v, onChange }: { v: View; onChange: (v: View) => void }) {
  const o = v.out_of_band;
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { setCode(""); setMsg(null); }, [v.case_id]);
  const na = v.decision.next_action;

  async function answer(a: "no" | "yes") {
    setMsg(null);
    const out = await call<{ ok: boolean; detail?: string; reason?: string; view?: View }>(
      `/api/try/phone/${o!.change_request_id}`, { method: "POST", body: JSON.stringify({ answer: a, code }) });
    if (out.ok && out.view) onChange(out.view); else setMsg(out.detail || out.reason || "Not accepted.");
  }

  let body: React.ReactNode = null;
  if (o && o.status === "pending_verification") {
    const sent = v.phone.find((p) => p.template === "vendor_verification");
    const suggested = sent?.body.match(/YES (\d{6})/)?.[1];
    body = (
      <>
        <p>Airlock texted the vendor at the phone number already on file (ending {o.phone_on_file_last4}), never a
          number from the email. You're holding that phone. Did the vendor really change banks?</p>
        {sent && <Phone text={sent.body} />}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button onClick={() => answer("no")} className="rounded-md bg-ok-soft px-3 py-1.5 font-semibold text-ok hover:brightness-110">Reply NO</button>
          <span className="text-muted">or</span>
          <input value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric"
            aria-label="One-time code" placeholder="6-digit code" className="num w-32 rounded-md border border-line bg-bg px-2 py-1.5" />
          <button onClick={() => answer("yes")} disabled={code.length !== 6}
            className="rounded-md border border-line px-3 py-1.5 hover:bg-raised disabled:opacity-50">Reply YES with code</button>
          {suggested && code.length === 0 && (
            <button onClick={() => setCode(suggested)} className="text-[13px] text-muted underline underline-offset-4">use the code from the text</button>)}
        </div>
        {msg && <p className="mt-2 text-bad">{msg}</p>}
      </>
    );
  } else if (o && o.status === "verified") {
    body = (
      <>
        <p>The vendor confirmed with the one-time code. That's evidence, not authority: the new account can only be
          used after an admin approves it on the authenticated approval screen (passcode, retype the new account's
          last 4, then a cancel window before any money moves).</p>
        {o.approval_link && (
          <p className="mt-2">
            <a href={o.approval_link} className="font-semibold underline underline-offset-4">Open the admin approval screen</a>
            <span className="text-muted"> (demo passcode: <span className="num text-text">{o.demo_passcode}</span>)</span>
          </p>)}
      </>
    );
  } else if (o && o.status === "rejected") {
    body = <p>The vendor said NO from the phone on file. The change is rejected; the invoice can only ever pay the
      account on file (ending {o.on_file_last4}). The vendor was warned that the mailbox may be compromised.</p>;
  } else if (o && o.status === "approved") {
    body = <p>An admin approved the new account. The held invoice is scheduled with a cancel window, then released.</p>;
  } else if (v.decision.decision === "deny") {
    body = <p>Nothing to approve. A sender outside the vendor registry can never be paid, whoever it claims to be. The
      admin is told; no reply goes to the sender.</p>;
  } else if (na?.startsWith("reply_in_thread:ask_vendor")) {
    body = <p>The vendor gets a templated reply asking for a corrected invoice. It pays only once the amount matches
      the approved order.</p>;
  } else if (na?.startsWith("request_receipt_confirmation")) {
    body = <p>The ops lead must confirm the goods arrived before this order can be paid.</p>;
  }
  if (!body) return null;
  return (
    <div className={`mt-5 rounded-md border p-4 text-[14px] leading-relaxed ${o?.status === "pending_verification" ? "border-held" : "border-line"}`}>
      <h3 className="mb-1 text-[15px] font-semibold">What a human has to do</h3>
      {body}
    </div>
  );
}

function Phone({ text }: { text: string }) {
  return (
    <div className="mt-3 max-w-[340px] rounded-[22px] border border-line bg-bg p-3">
      <p className="mb-2 text-center text-[11px] text-muted">Vendor's phone on file (simulated)</p>
      <p className="rounded-2xl rounded-bl-sm bg-raised px-3 py-2 text-[13px] leading-snug whitespace-pre-wrap">{text}</p>
    </div>
  );
}

function Moved({ v }: { v: View }) {
  const e = v.executed;
  const asked = v.asked.accounts;
  const sim = (id: string | null) => (id?.startsWith("tr_sim_") ? " (simulated rail)" : " (Stripe test mode)");
  return (
    <div className="mt-5">
      <h3 className="text-[13px] text-muted">What actually moved</h3>
      <ul className="mt-1 space-y-1 text-[15px]">
        {e && e.paid_cents > 0 && (
          <li className="text-ok"><span className="num font-semibold">{usd(e.paid_cents)}</span> to the account on file, ending {e.paid_to_last4}
            <span className="text-[12px] text-muted"> transfer {e.transfer_id}{sim(e.transfer_id)}</span></li>)}
        {(e?.refused_last4s ?? asked).filter((a) => !e || a !== e.paid_to_last4).map((a) => (
          <li key={a}><span className="num font-semibold">$0.00</span> to account ending {a}, the one the email asked for</li>))}
        {(!e || e.paid_cents === 0) && asked.length === 0 && <li><span className="num font-semibold">$0.00</span>. Nothing was paid.</li>}
      </ul>
    </div>
  );
}

function Thread({ v }: { v: View }) {
  return (
    <details className="mt-4 rounded-lg border border-line bg-panel">
      <summary className="cursor-pointer px-4 py-3 text-[14px]">The agents' case thread ({v.thread.length} messages)</summary>
      <ol className="divide-y divide-line border-t border-line">
        {v.thread.map((p, i) => (
          <li key={i} className="px-4 py-3">
            <p className="text-[13px] font-semibold capitalize">{p.author}
              <span className="font-normal text-muted">{p.author === "auditor" ? ", policy code" : p.author === "treasurer" ? ", agent with payment tools" : ""}</span></p>
            <p className="mt-1 text-[14px] leading-relaxed whitespace-pre-wrap text-code">{p.body}</p>
          </li>
        ))}
        {v.admin_briefs.map((a, i) => (
          <li key={`a${i}`} className="px-4 py-3">
            <p className="text-[13px] font-semibold">Text to the Kiln admin <span className="font-normal text-muted">(simulated)</span></p>
            <p className="mt-1 text-[14px] leading-relaxed text-code">{a.body}</p>
          </li>
        ))}
      </ol>
    </details>
  );
}
