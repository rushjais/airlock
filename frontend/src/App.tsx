import { useEffect, useRef, useState } from "react";
import {
  api, usd, type Card, type CaseDetail, type CaseRow, type Controls, type ModeResult, type Money, type Post,
  type Results, type Summary,
} from "./api";

const POLL_MS = 1500;
const INBOX_DOMAIN = "wise-egret.primitive.email";
const AGENTS = ["intake", "auditor", "treasurer", "verifier"];

function usePoll<T>(fn: () => Promise<T>, deps: unknown[] = []): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = () => fn().then((d) => alive && setData(d)).catch(() => {});
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => { alive = false; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return data;
}

function useTheme(): [boolean, () => void] {
  const [light, setLight] = useState(() => {
    try { return localStorage.getItem("airlock-theme") === "light"; } catch { return false; }
  });
  useEffect(() => {
    document.documentElement.classList.toggle("light", light);
    try { localStorage.setItem("airlock-theme", light ? "light" : "dark"); } catch { /* storage blocked */ }
  }, [light]);
  return [light, () => setLight((v) => !v)];
}

const STATUS: Record<CaseRow["status"], { label: string; cls: string }> = {
  paid: { label: "Paid", cls: "bg-ok-soft text-ok" },
  held: { label: "Held", cls: "bg-held-soft text-held" },
  denied: { label: "Blocked", cls: "bg-bad-soft text-bad" },
  needs_approval: { label: "Needs approval", cls: "bg-held-soft text-held" },
  open: { label: "Checking", cls: "bg-raised text-muted" },
};

export default function App() {
  const [light, toggleTheme] = useTheme();
  const [view, setView] = useState<"live" | "results">("live");
  const cases = usePoll(api.cases) ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const [follow, setFollow] = useState(true);
  const selected = follow ? cases[0]?.id ?? null : picked;

  return (
    <div className="h-full grid grid-rows-[auto_1fr] bg-bg">
      <TopBar view={view} onView={setView} light={light} onTheme={toggleTheme} />
      {view === "results" ? <ResultsView /> : (
        <main className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1.2fr)_minmax(300px,0.72fr)] gap-4 p-4 min-h-0">
          <Threads cases={cases} selected={selected} follow={follow}
                   onPick={(id) => { setPicked(id); setFollow(false); }} onFollow={() => setFollow(true)} />
          <DecisionCard caseId={selected} />
          <MoneyPanel cases={cases} />
        </main>
      )}
    </div>
  );
}

/* ---------- Top bar: totals that stay visible the whole demo ---------- */

function TopBar(p: { view: "live" | "results"; onView: (v: "live" | "results") => void; light: boolean; onTheme: () => void }) {
  const s = usePoll<Summary>(api.summary);
  const [resetting, setResetting] = useState(false);
  const reset = async () => {
    if (!confirm("Reset the demo to fresh fixtures? This clears all cases.")) return;
    setResetting(true);
    await api.reset();
    setResetting(false);
  };
  const tab = (v: "live" | "results", label: string) => (
    <button onClick={() => p.onView(v)} aria-pressed={p.view === v}
      className={`px-3.5 py-1.5 rounded-md text-[15px] font-semibold border ${p.view === v ? "bg-text text-bg border-text" : "border-line text-muted hover:text-text"}`}>
      {label}
    </button>
  );
  return (
    <header className="flex items-center justify-between gap-6 px-6 py-3.5 border-b-2 border-line bg-panel">
      <div className="flex items-baseline gap-4 min-w-0">
        <h1 className="wide text-[30px] font-extrabold tracking-tight leading-none">Airlock</h1>
        <span className="text-muted text-[17px] truncate">{s?.company || "Kiln Supply Co."}</span>
      </div>
      <dl className="flex items-baseline gap-8">
        <Total label="Paid" cents={s?.paid_cents ?? 0} cls="text-ok" />
        <Total label="Held" cents={s?.held_cents ?? 0} cls="text-held" />
        <Total label="Stolen" cents={s?.stolen_cents ?? 0} cls={(s?.stolen_cents ?? 0) > 0 ? "text-bad" : "text-ok"} big />
      </dl>
      <div className="flex items-center gap-2">
        {DEMO && <DemoButtons />}
        {tab("live", "Live")}{tab("results", "Measured results")}
        <button onClick={p.onTheme} className="px-3 py-1.5 rounded-md text-[15px] font-semibold border border-line text-muted hover:text-text">
          {p.light ? "Dark" : "Light"}
        </button>
        <button onClick={reset} disabled={resetting}
          className="px-3 py-1.5 rounded-md text-[15px] font-semibold border border-line text-muted hover:text-text disabled:opacity-50">
          {resetting ? "Resetting" : "Reset demo"}
        </button>
      </div>
    </header>
  );
}

const DEMO = new URLSearchParams(location.search).has("demo");
const SCENES: [string, string][] = [
  ["01_legit_invoice", "Legit invoice"],
  ["02_hacked_vendor_bank_change", "Hacked bank change"],
  ["04_hidden_pdf_instruction", "Hidden PDF trick"],
  ["06_replay", "Duplicate"],
];

/* Backup for live demos (only with ?demo in the URL): push a scenario email through the real pipeline. */
function DemoButtons() {
  const [busy, setBusy] = useState<string | null>(null);
  return (
    <div className="flex items-center gap-1.5 mr-3">
      <button disabled={busy !== null}
        onClick={async () => { setBusy("dispute"); await api.vendorDispute(); setTimeout(() => setBusy(null), 1500); }}
        className="px-2.5 py-1.5 rounded-md text-[13px] font-semibold border border-dashed border-line text-muted hover:text-text disabled:opacity-50">
        {busy === "dispute" ? "Sending" : "Price dispute"}
      </button>
      {SCENES.map(([id, label]) => (
        <button key={id} disabled={busy !== null}
          onClick={async () => { setBusy(id); await api.replay(id); setTimeout(() => setBusy(null), 1500); }}
          className="px-2.5 py-1.5 rounded-md text-[13px] font-semibold border border-dashed border-line text-muted hover:text-text disabled:opacity-50">
          {busy === id ? "Sending" : label}
        </button>
      ))}
    </div>
  );
}

function Total({ label, cents, cls, big }: { label: string; cents: number; cls: string; big?: boolean }) {
  return (
    <div className="flex items-baseline gap-2.5">
      <dt className="text-muted text-[16px] font-semibold">{label}</dt>
      <dd className={`num font-bold ${big ? "text-[34px]" : "text-[24px]"} ${cls}`}>{usd(cents)}</dd>
    </div>
  );
}

/* ---------- Left: live Primitive threads ---------- */

const WHO: Record<string, { name: string; role: string }> = {
  intake: { name: "Intake", role: "reads the email, no money access" },
  auditor: { name: "Auditor", role: "Airlock policy, deterministic code" },
  treasurer: { name: "Treasurer", role: "agent with Airlock's payment tools" },
  verifier: { name: "Verifier", role: "contacts the phone already on file" },
  admin: { name: "Admin", role: "human reply in the thread" },
  external: { name: "Outside agent", role: "connected through Airlock's MCP server" },
  vendor: { name: "Vendor's agent", role: "Corvid Software's billing agent, by email" },
  unverified: { name: "Unsigned message", role: "not from an Airlock agent" },
};

function Threads(p: { cases: CaseRow[]; selected: string | null; follow: boolean; onPick: (id: string) => void; onFollow: () => void }) {
  return (
    <section className="min-h-0 grid grid-rows-[auto_minmax(0,1fr)] rounded-lg border-2 border-line bg-panel overflow-hidden">
      <div className="border-b-2 border-line">
        <div className="flex items-baseline justify-between px-5 pt-3.5 pb-2">
          <h2 className="text-[19px] font-bold">Agent threads</h2>
          {!p.follow && <button onClick={p.onFollow} className="text-[14px] text-muted underline underline-offset-4">Follow newest</button>}
        </div>
        <ol className="max-h-[168px] overflow-y-auto px-2 pb-2">
          {p.cases.length === 0 && <li className="px-3 py-3 text-muted text-[16px]">No email yet. Send an invoice to {`acme@${INBOX_DOMAIN}`}.</li>}
          {p.cases.map((c) => {
            const s = STATUS[c.status];
            return (
              <li key={c.id}>
                <button onClick={() => p.onPick(c.id)}
                  className={`w-full flex items-center justify-between gap-3 px-3 py-2 rounded-md text-left ${c.id === p.selected ? "bg-raised ring-2 ring-line" : "hover:bg-raised/60"}`}>
                  <span className="min-w-0 truncate text-[16px]"><b>{vendorOf(c)}</b> <span className="text-muted">{invoiceOf(c)}</span></span>
                  <span className={`shrink-0 rounded px-2 py-0.5 text-[13px] font-bold ${s.cls}`}>{s.label}</span>
                </button>
              </li>
            );
          })}
        </ol>
      </div>
      <Thread caseId={p.selected} />
    </section>
  );
}

const vendorOf = (c: CaseRow) => c.subject.replace(/^\[[^\]]+\]\s*/, "").split(":")[0];
const invoiceOf = (c: CaseRow) => c.subject.split(":").slice(1).join(":").trim();
const isFresh = (iso: string) => Date.now() - new Date(iso).getTime() < 4000;
const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });

function Thread({ caseId }: { caseId: string | null }) {
  const detail = usePoll<CaseDetail | null>(() => (caseId ? api.case(caseId) : Promise.resolve(null)), [caseId]);
  const end = useRef<HTMLDivElement>(null);
  const n = detail?.posts.length ?? 0;
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [n, caseId]);
  if (!detail) return <div className="p-5 text-muted text-[16px]">Pick a case to read its thread.</div>;
  return (
    <div className="overflow-y-auto min-h-0 px-5 py-4">
      <p className="num text-[13px] text-muted mb-4 truncate">{detail.case.id}@{INBOX_DOMAIN}</p>
      <ol className="space-y-4">
        {detail.posts.map((post, i) => <PostView key={`${detail.case.id}-${i}`} post={post} caseId={detail.case.id} />)}
      </ol>
      <div ref={end} />
    </div>
  );
}

function PostView({ post, caseId }: { post: Post; caseId: string }) {
  const who = WHO[post.author] ?? { name: post.author, role: "" };
  const isCode = post.author === "auditor";
  const isAgent = AGENTS.includes(post.author);
  const verdict = isCode ? post.body.split(":")[0] : null;
  const edge = verdict === "APPROVED" ? "border-ok" : verdict === "DENIED" ? "border-bad" : verdict ? "border-held"
             : post.author === "unverified" ? "border-bad" : "border-line";
  const cc = AGENTS.filter((a) => a !== post.author).map((a) => `${a}@`).join(", ");
  return (
    <li className={`border-l-4 ${edge} pl-4 pr-2 py-1 ${isFresh(post.created_at) ? "fresh" : ""}`}>
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
        <span className="text-[17px] font-bold">{who.name}</span>
        <span className="text-[13px] text-muted">{who.role}</span>
        <span className="num text-[13px] text-muted ml-auto">{time(post.created_at)}</span>
      </div>
      {isAgent && (
        <p className="num text-[12px] text-muted mb-1.5 truncate">
          {post.author}@ to {caseId.slice(0, 18)}…@ cc {cc}
        </p>
      )}
      {isCode
        ? <pre className="font-code text-[14.5px] leading-[1.55] whitespace-pre-wrap text-code">{post.body}</pre>
        : <p className="font-letter text-[18px] leading-[1.5] whitespace-pre-wrap">{post.body}</p>}
    </li>
  );
}

/* ---------- Middle: the decision card ---------- */

const VERDICT: Record<string, { word: string; cls: string }> = {
  pay: { word: "Paid", cls: "text-ok" },
  hold: { word: "Held", cls: "text-held" },
  deny: { word: "Blocked", cls: "text-bad" },
  approval_required: { word: "Needs approval", cls: "text-held" },
  pending_approval: { word: "Needs approval", cls: "text-held" },
};

function fmt(v: string | number | null | undefined, money?: boolean) {
  if (v === null || v === undefined) return "-";
  return money && typeof v === "number" ? usd(v) : String(v);
}

function DecisionCard({ caseId }: { caseId: string | null }) {
  const card = usePoll<Card | null>(() => (caseId ? api.card(caseId) : Promise.resolve(null)), [caseId]);
  if (!card) return <section className="rounded-lg border-2 border-line bg-panel p-6 text-muted text-[16px]">The decision for the selected case shows here.</section>;
  const v = VERDICT[card.decision ?? ""] ?? { word: card.decision ?? "", cls: "text-muted" };
  const word = card.case_status === "paid" ? VERDICT.pay : v;
  return (
    <section className="min-h-0 overflow-y-auto rounded-lg border-2 border-line bg-panel p-6">
      <h2 className="text-[19px] font-bold mb-1">Decision</h2>
      <p className={`wide font-extrabold text-[48px] leading-none mb-5 ${word.cls}`}>{word.word}</p>

      <table className="w-full text-[17px] mb-6 border-collapse">
        <thead>
          <tr className="text-muted text-[14px] text-left">
            <th className="font-semibold pb-2 w-[17%]"></th>
            <th className="font-semibold pb-2">The email claims</th>
            <th className="w-8"></th>
            <th className="font-semibold pb-2">Company records</th>
          </tr>
        </thead>
        <tbody>
          {card.rows.map((r) => (
            <tr key={r.label} className="border-t border-line align-top">
              <td className="py-2.5 pr-2 font-semibold text-muted">{r.label}</td>
              <td className={`py-2.5 pr-2 num text-[15.5px] [overflow-wrap:anywhere] ${r.match ? "text-text" : "text-bad font-bold"}`}>{fmt(r.claim, r.money)}</td>
              <td className={`py-2.5 text-center font-bold text-[20px] ${r.match ? "text-ok" : "text-bad"}`}>{r.match ? "=" : "≠"}</td>
              <td className="py-2.5 num text-[15.5px] text-ok [overflow-wrap:anywhere]">
                {fmt(r.record, r.money)}{r.record_note ? <span className="text-muted"> {r.record_note}</span> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <dl className="space-y-3 text-[16px]">
        <div><dt className="text-muted font-semibold mb-1">Rule that fired</dt><dd className="num text-[16px]">{card.reason}</dd></div>
        <div><dt className="text-muted font-semibold mb-1">Next action</dt><dd className="num text-[16px] [overflow-wrap:anywhere]">{card.case_status === "paid" ? "none (paid)" : card.next_action}</dd></div>
        {card.flags.length > 0 && (
          <div><dt className="text-muted font-semibold mb-1.5">Flags</dt>
            <dd className="flex flex-wrap gap-1.5">
              {card.flags.map((f) => <span key={f} className="num text-[13px] px-2 py-0.5 rounded bg-bad-soft text-bad font-semibold">{f}</span>)}
            </dd>
          </div>
        )}
      </dl>
      <p className="text-[13px] text-muted mt-6">Decided by deterministic code from the vendor registry and approved purchase orders. No model votes on pay or hold.</p>
    </section>
  );
}

/* ---------- Right: money and spending controls ---------- */

function MoneyPanel({ cases }: { cases: CaseRow[] }) {
  const money = usePoll<Money>(api.money);
  const ctl = usePoll<Controls>(api.controls);
  const paid = (money?.payments ?? []).filter((p) => p.status === "succeeded");
  const held = cases.filter((c) => c.status === "held" || c.status === "needs_approval");
  return (
    <section className="min-h-0 overflow-y-auto rounded-lg border-2 border-line bg-panel p-5 space-y-6">
      <div>
        <h2 className="text-[19px] font-bold mb-3">Money</h2>
        <ol className="space-y-2.5">
          {paid.length === 0 && held.length === 0 && <li className="text-muted text-[15px]">No payments yet.</li>}
          {paid.map((p) => (
            <li key={p.id} className={`flex items-baseline justify-between gap-3 ${isFresh(p.created_at) ? "fresh" : ""}`}>
              <div className="min-w-0">
                <div className="text-[16px] font-semibold"><span className="text-ok">✓</span> {p.vendor}</div>
                <div className="num text-[12px] text-muted truncate">{p.stripe_transfer_id}</div>
              </div>
              <div className="num text-[17px] font-bold text-ok">{usd(p.amount_cents)}</div>
            </li>
          ))}
          {held.map((c) => (
            <li key={c.id} className="flex items-baseline justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[16px] font-semibold"><span className="text-held">⏸</span> {vendorOf(c)}</div>
                <div className="text-[12px] text-muted truncate">{invoiceOf(c)}, {STATUS[c.status].label.toLowerCase()}</div>
              </div>
              <div className="num text-[17px] font-bold text-held">{c.amount_cents ? usd(c.amount_cents) : "-"}</div>
            </li>
          ))}
        </ol>
      </div>

      <div className="border-t-2 border-line pt-5">
        <h2 className="text-[19px] font-bold mb-3">Spending controls</h2>
        <dl className="space-y-3 text-[16px]">
          <div className="flex justify-between gap-3"><dt className="text-muted">Pays automatically up to</dt><dd className="num font-bold">{usd(ctl?.auto_pay_limit_cents ?? 0)}</dd></div>
          <div className="flex justify-between gap-3"><dt className="text-muted">Undo window after approval</dt><dd className="num font-bold">{ctl?.undo_window_seconds ?? 0}s</dd></div>
          <div className="flex justify-between gap-3"><dt className="text-muted">Waiting for a human</dt>
            <dd className={`num font-bold ${(ctl?.approval_queue ?? 0) > 0 ? "text-held" : ""}`}>{ctl?.approval_queue ?? 0}</dd></div>
        </dl>
        {(ctl?.standing_limits ?? []).map((l) => (
          <div key={l.vendor + l.line_item} className="mt-4">
            <div className="flex justify-between text-[14px]"><span className="text-muted">{l.vendor}: {l.line_item}</span>
              <span className="num">{usd(l.used_cents)} / {usd(l.monthly_cap_cents)}</span></div>
            <div className="h-2 rounded bg-raised mt-1.5 overflow-hidden">
              <div className="h-full bg-ok" style={{ width: `${Math.min(100, (l.used_cents / Math.max(1, l.monthly_cap_cents)) * 100)}%` }} />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ---------- Measured results: the same emails, three agents ---------- */

function ResultsView() {
  const r = usePoll<Results>(api.results);
  if (!r) return null;
  if (!r.available) return <p className="p-10 text-muted text-[18px]">No measurement yet. Run backend/evaluate.py.</p>;
  const cols: [string, string, ModeResult | undefined, boolean][] = [
    ["Agent told to pay invoices as instructed", "Same model, a raw pay tool, no Airlock", r.baseline_permissive, false],
    ["Agent told to watch for fraud", "Same model, a raw pay tool, no Airlock", r.baseline, false],
    ["Agent with Airlock", "Same model, Airlock's tools only", r.airlock, true],
  ];
  return (
    <main className="overflow-y-auto px-8 py-7">
      <p className="text-[18px] text-muted max-w-[72ch] mb-7">
        The same {r.airlock?.emails} emails through each agent, with real Stripe test transfers: {r.airlock?.non_payable_emails} attacks
        or payments that aren't owed, and {r.airlock?.legit_invoices} legitimate invoices.
      </p>
      <div className="grid grid-cols-3 gap-4">
        {cols.map(([title, sub, m, ours]) => m && (
          <section key={title} className={`rounded-lg border-2 p-6 bg-panel ${ours ? "border-ok" : "border-line"}`}>
            <h2 className="text-[21px] font-bold leading-tight">{title}</h2>
            <p className="text-[14px] text-muted mb-6">{sub}</p>
            <p className="text-[15px] text-muted">Sent to attackers</p>
            <p className={`num font-bold text-[48px] leading-tight mb-5 ${m.cents_to_attacker ? "text-bad" : "text-ok"}`}>{usd(m.cents_to_attacker)}</p>
            {m === r.baseline_permissive && r.trick_rate && (
              <div className="rounded-md bg-bad-soft px-4 py-3 mb-5">
                <p className="text-[15px] text-bad font-semibold">Repeated on the bank-redirect emails</p>
                <p className="text-[17px]">Fooled in <span className="num font-bold text-bad">{r.trick_rate.fooled} of {r.trick_rate.attempts}</span> tries,{" "}
                  <span className="num font-bold text-bad">{usd(r.trick_rate.cents_to_attacker)}</span> sent to attackers</p>
              </div>
            )}
            <dl className="space-y-2.5 text-[17px]">
              <div className="flex justify-between gap-4"><dt>Payments that shouldn't have happened</dt>
                <dd className={`num font-bold ${m.unauthorized_payments ? "text-bad" : ""}`}>{m.unauthorized_payments}</dd></div>
              <div className="flex justify-between gap-4"><dt>Legitimate invoices paid</dt>
                <dd className={`num font-bold ${m.legit_paid < m.legit_invoices ? "text-held" : "text-ok"}`}>{m.legit_paid} of {m.legit_invoices}</dd></div>
            </dl>
          </section>
        ))}
      </div>
      <p className="text-[13px] text-muted mt-6">Run {r.run_id}. Model outputs vary between runs; these numbers are from this run only.</p>
    </main>
  );
}
