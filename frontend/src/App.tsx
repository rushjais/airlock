import { useEffect, useRef, useState } from "react";
import {
  api, usd, type Card, type CaseDetail, type CaseRow, type Controls, type ModeResult, type Money, type Post,
  type Results, type Summary, type VendorBooks,
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
  resolved: { label: "Resolved", cls: "bg-ok-soft text-ok" },
};

export default function App() {
  const [light, toggleTheme] = useTheme();
  const [view, setView] = useState<"live" | "results">("live");
  const cases = usePoll(api.cases) ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const [follow, setFollow] = useState(true);
  const selected = follow ? cases[0]?.id ?? null : picked;

  return (
    <div className={`h-full grid ${DEMO ? "grid-rows-[auto_auto_1fr]" : "grid-rows-[auto_1fr]"} bg-bg`}>
      <TopBar view={view} onView={setView} light={light} onTheme={toggleTheme} />
      {DEMO && <DemoButtons />}
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
        <span className="text-muted text-[17px] whitespace-nowrap">{s?.company || "Kiln Supply Co."}</span>
      </div>
      <dl className="flex items-baseline gap-8">
        <Total label="Paid" cents={s?.paid_cents ?? 0} cls="text-ok" />
        <Total label="Held" cents={s?.held_cents ?? 0} cls="text-held" />
        <Total label="Stolen" cents={s?.stolen_cents ?? 0} cls={(s?.stolen_cents ?? 0) > 0 ? "text-bad" : "text-ok"} big />
      </dl>
      <div className="flex items-center gap-2">
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
      <button disabled={busy !== null}
        onClick={async () => { setBusy("forged"); await api.forgedAuditor(); setTimeout(() => setBusy(null), 1500); }}
        className="px-2.5 py-1.5 rounded-md text-[13px] font-semibold border border-dashed border-line text-muted hover:text-text disabled:opacity-50">
        {busy === "forged" ? "Sending" : "Forged auditor"}
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
  inbound: { name: "Inbound email", role: "arrived at Kiln's AP inbox" },
  outbound: { name: "Reply to vendor", role: "Kiln AP, from a template" },
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
          {p.cases.length === 0 && <li className="px-3 py-3 text-muted text-[16px]">No email yet. Send an invoice to {`ap@${INBOX_DOMAIN}`}.</li>}
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
      <p className="num text-[13px] text-muted mb-2 truncate">{detail.case.id}@{INBOX_DOMAIN}</p>
      <RoutingStrip posts={detail.posts} status={detail.case.status} />
      <ol className="space-y-4">
        {detail.posts.map((post, i) => <PostView key={`${detail.case.id}-${i}`} post={post} caseId={detail.case.id} />)}
      </ol>
      <div ref={end} />
    </div>
  );
}

/* Who handed the case to whom, lit up as the agents post (the email traffic, at a glance). */
function RoutingStrip({ posts, status }: { posts: Post[]; status: CaseRow["status"] }) {
  const has = (a: string) => posts.some((p) => p.author === a);
  const verdict = posts.filter((p) => p.author === "auditor").map((p) => p.body.split(":")[0]).pop();
  const vTone = verdict === "APPROVED" ? "ok" : verdict === "DENIED" || verdict === "IGNORED" ? "bad" : verdict ? "held" : "";
  const end = status === "paid" ? { label: "Stripe ✓", tone: "ok" } : status === "denied" ? { label: "Blocked", tone: "bad" }
            : status === "held" || status === "needs_approval" ? { label: "Held", tone: "held" }
            : status === "resolved" ? { label: "Resolved", tone: "ok" } : null;
  const nodes: { label: string; on: boolean; tone?: string }[] = [
    { label: has("unverified") ? "Forged mail" : "Email", on: has("inbound") || has("unverified"), tone: "bad" },
    { label: "intake@", on: has("intake") },
    { label: "auditor@", on: !!verdict, tone: vTone },
    ...(has("verifier") ? [{ label: "verifier@ → phone", on: true }] : []),
    { label: "treasurer@", on: has("treasurer") },
    ...(has("outbound") ? [{ label: "reply to vendor", on: true }] : []),
    ...(end ? [{ label: end.label, on: true, tone: end.tone }] : []),
  ];
  const cls = (n: { on: boolean; tone?: string }) => !n.on ? "border-line text-muted opacity-50"
    : n.tone === "ok" ? "border-ok text-ok bg-ok-soft" : n.tone === "bad" ? "border-bad text-bad bg-bad-soft"
    : n.tone === "held" ? "border-held text-held bg-held-soft" : "border-text/40 text-text bg-raised";
  return (
    <ol aria-label="How this case moved between agents" className="mb-4 flex flex-wrap items-center gap-x-1 gap-y-1.5">
      {nodes.map((n, i) => (
        <li key={n.label + i} className="flex items-center gap-1">
          {i > 0 && <span className={`text-[12px] ${n.on ? "text-muted" : "text-line"}`}>→</span>}
          <span className={`num rounded-md border px-2 py-0.5 text-[12px] font-semibold transition-colors duration-500 ${cls(n)}`}>{n.label}</span>
        </li>
      ))}
    </ol>
  );
}

function short(addr?: string | null) {
  if (!addr) return "";
  return addr.endsWith(`@${INBOX_DOMAIN}`) ? addr.replace(`@${INBOX_DOMAIN}`, "@").replace(/^(case-[a-z0-9-]{0,14}).*@$/, "$1…@") : addr;
}

function PostView({ post }: { post: Post; caseId: string }) {
  const who = WHO[post.author] ?? { name: post.author, role: "" };
  const env = post.envelope;
  const isCode = post.author === "auditor";
  const isInbound = post.author === "inbound";
  const isOut = post.author === "outbound";
  const verdict = isCode ? post.body.split(":")[0] : null;
  const edge = verdict === "APPROVED" ? "border-ok" : verdict === "DENIED" ? "border-bad" : verdict ? "border-held"
             : post.author === "unverified" || isInbound ? "border-bad" : isOut ? "border-muted" : "border-line";
  const [open, setOpen] = useState(false);
  const [more, setMore] = useState(false);
  const long = !isCode && post.body.length > 360;
  const cut = post.body.lastIndexOf(" ", 260);
  const shortBody = post.body.slice(0, cut > 0 ? cut : 260) + "…";
  const atts = env?.attachments ?? [];
  return (
    <li className={`border-l-4 ${edge} pl-4 pr-2 py-1 ${isFresh(post.created_at) ? "fresh" : ""}`}>
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
        <span className="text-[17px] font-bold">{who.name}</span>
        <span className="text-[13px] text-muted">{who.role}</span>
        <span className="num text-[13px] text-muted ml-auto">{time(post.created_at)}</span>
      </div>
      {env?.from && (
        <button onClick={() => setOpen(!open)} aria-expanded={open}
                className="group flex w-full items-center gap-2 text-left num text-[12px] text-muted mb-1.5 hover:text-text">
          <span className="truncate">{short(env.from)} → {short(env.to)}{env.cc.length ? ` · cc ${env.cc.map(short).join(", ")}` : ""}</span>
          {env.signed && <span className="shrink-0 rounded px-1.5 py-px bg-ok-soft text-ok font-semibold">signed</span>}
          {isInbound && <span className="shrink-0 rounded px-1.5 py-px bg-bad-soft text-bad font-semibold">untrusted</span>}
          {atts.length > 0 && <span className="shrink-0">📎 {atts.length}</span>}
          <span className="shrink-0 ml-auto underline underline-offset-2">{open ? "hide" : "details"}</span>
        </button>
      )}
      {open && env && (
        <dl className="mb-2.5 grid grid-cols-[86px_minmax(0,1fr)] gap-x-3 gap-y-1 rounded-md border border-line bg-raised px-3 py-2.5 text-[13px]">
          <dt className="text-muted">From</dt><dd className="num [overflow-wrap:anywhere]">{env.from}</dd>
          <dt className="text-muted">To</dt><dd className="num [overflow-wrap:anywhere]">{env.to}</dd>
          {env.cc.length > 0 && <><dt className="text-muted">Cc</dt><dd className="num [overflow-wrap:anywhere]">{env.cc.join(", ")}</dd></>}
          {env.reply_to && <><dt className="text-muted">Reply-To</dt><dd className="num [overflow-wrap:anywhere]">{env.reply_to}</dd></>}
          <dt className="text-muted">Subject</dt><dd className="[overflow-wrap:anywhere]">{env.subject}</dd>
          {atts.length > 0 && <><dt className="text-muted">Attached</dt><dd>{atts.map((a) => (
            <div key={a.name} className="flex flex-wrap items-center gap-1.5"><span className="num">{a.name}</span>
              <span className="rounded px-1.5 py-px bg-held-soft text-held text-[11px] font-semibold">{a.label}</span>
              <span className="text-muted">{a.note}</span></div>))}</dd></>}
          {env.template && <><dt className="text-muted">Template</dt><dd className="num">{env.template}</dd></>}
          <dt className="text-muted">Trust</dt><dd>{env.signed ? "HMAC-signed by Airlock; verified when it came back through Primitive"
            : isInbound ? "None. Sender domain is a routing hint, not identity" : isOut ? "Sent by Airlock to the thread sender only" : "Not signed"}
            {env.delivered ? " · delivered by Primitive" : ""}</dd>
          <dt className="text-muted">Why</dt><dd className="font-semibold">{env.why}</dd>
        </dl>
      )}
      {open && env && <div className="text-[11px] uppercase tracking-wider text-muted mb-1">{isInbound ? "Email body (claims)" : "Body · shared context for the next agent"}</div>}
      {isCode
        ? <pre className="font-code text-[14.5px] leading-[1.55] whitespace-pre-wrap text-code">{post.body}</pre>
        : <p className={`font-letter ${isInbound || isOut ? "text-[16px] text-muted" : "text-[18px]"} leading-[1.5] whitespace-pre-wrap`}>{long && !more ? shortBody : post.body}</p>}
      {long && (
        <button onClick={() => setMore(!more)} className="text-[13px] text-muted hover:text-text mt-1 underline underline-offset-2">
          {more ? "Show less" : "Show all"}</button>
      )}
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

// Plain-English gloss for each policy rule (the slug stays visible: it's what the code checked).
const WHY: Record<string, string> = {
  document_from_different_vendor: "The attached invoice belongs to a different company than the sender. Asked them to send their own.",
  matched_accepted_obligation: "Sender, amount and PO match an approved, received order. Paid to the account on file.",
  amount_mismatch: "The amount doesn't match what was approved for this PO. Asked the vendor to explain or reissue.",
  destination_change_requested: "The email asks to pay a different account. Checking with the vendor's phone on file.",
  obligation_not_accepted: "The order is approved but the goods haven't been received yet.",
  obligation_already_settled: "This order was already paid. No second payment.",
  obligation_already_reserved: "A payment for this order is already in progress. No second payment.",
  obligation_cancelled: "This order was cancelled.",
  sender_not_registered: "The sender's domain isn't a registered vendor (it imitates one).",
  unknown_vendor: "Not a registered vendor. Onboarding needs a human.",
  no_obligation: "No approved order covers this charge. Needs a human to approve it first.",
  over_auto_pay_limit: "Above the auto-pay limit. Waiting for a human.",
  outbound_recipient_not_registered: "Asks us to send internal documents to an address that isn't on file.",
  unsigned_agent_mail: "Claims to be one of our agents but isn't signed by Airlock.",
  change_rejected_paying_registry_destination: "The vendor's phone on file said they didn't change banks. Paid the account on file.",
  change_rejected_held_by_policy: "The vendor said they didn't change banks. Your policy holds the invoice for you.",
  change_rejected_invoice_needs_review: "Change rejected, but the invoice itself needs a human look.",
  change_rejected_no_invoice: "Change rejected. There was no invoice to pay.",
  reader_unavailable: "Couldn't read the email safely, so nothing can pay. It will be retried.",
};

/* The out-of-band check as evidence bound to one exact change (what an Inkbox "verified action" would return). */
function VerifiedAction({ v }: { v: NonNullable<Card["verification"]> }) {
  const tone = v.answer === "no" ? "text-ok" : v.answer === "yes" ? "text-held" : v.answer === "waiting" ? "text-muted" : "text-held";
  const row = (k: string, val: React.ReactNode) => val ? (<><dt className="text-muted">{k}</dt><dd className="[overflow-wrap:anywhere]">{val}</dd></>) : null;
  return (
    <div className="mt-6 rounded-lg border-2 border-line bg-raised p-4">
      <div className="flex items-baseline justify-between gap-3 mb-3">
        <h3 className="text-[16px] font-bold">Verified action <span className="text-muted font-normal text-[13px]">· evidence, not authority</span></h3>
        <span className={`num text-[13px] font-bold uppercase ${tone}`}>{v.answer === "waiting" ? "waiting…" : `vendor: ${v.answer}`}</span>
      </div>
      <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[14px]">
        {row("Action", <span><span className="font-semibold">{v.action}</span> <span className="num">••{v.from_last4} → <span className="text-bad">••{v.to_last4}</span></span></span>)}
        {row("Asked", v.contact && <span>{v.contact} <span className="text-muted">({v.contact_source})</span></span>)}
        {row("Channel", v.channel && <span className="num">Inkbox {v.channel}{v.asked_at ? ` · ${time(v.asked_at)}` : ""}</span>)}
        {row("Answer", v.reply && <span className="font-letter text-[16px]">"{v.reply}"</span>)}
        {row("Transcript", v.transcript && <span className="text-muted text-[13px]">{v.transcript}</span>)}
        {row("Bound to", <span className="num text-[13px]">{v.bound_to}</span>)}
      </dl>
      <p className="mt-3 border-t border-line pt-3 text-[14.5px]"><span className="text-muted">Airlock's decision: </span><span className="font-semibold">{v.outcome}</span></p>
    </div>
  );
}

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
        <div><dt className="text-muted font-semibold mb-1">Rule that fired</dt><dd className="num text-[16px]">{card.reason}</dd>
          {card.reason && WHY[card.reason] && <dd className="text-[15px] text-muted mt-1">{WHY[card.reason]}</dd>}</div>
        <div><dt className="text-muted font-semibold mb-1">Next action</dt><dd className="num text-[16px] [overflow-wrap:anywhere]">{card.case_status === "paid" ? "none (paid)" : card.next_action}</dd></div>
        {card.flags.length > 0 && (
          <div><dt className="text-muted font-semibold mb-1.5">Flags</dt>
            <dd className="flex flex-wrap gap-1.5">
              {card.flags.map((f) => <span key={f} className="num text-[13px] px-2 py-0.5 rounded bg-bad-soft text-bad font-semibold">{f}</span>)}
            </dd>
          </div>
        )}
      </dl>
      {card.verification && <VerifiedAction v={card.verification} />}
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
        <AfterRejected value={ctl?.after_rejected_change} />
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
      <VendorBooksView />
    </section>
  );
}

/* Human policy: when the phone on file says "we did NOT change banks", pay the registry account or wait for a human. */
function AfterRejected({ value }: { value?: "pay_on_file" | "hold" }) {
  const [pending, setPending] = useState<string | null>(null);
  const cur = pending ?? value;
  const pick = async (v: "pay_on_file" | "hold") => { setPending(v); await api.setAfterRejected(v); setTimeout(() => setPending(null), 2000); };
  const opt = (v: "pay_on_file" | "hold", label: string) => (
    <button onClick={() => pick(v)} aria-pressed={cur === v}
            className={`flex-1 px-2 py-1.5 rounded text-[13px] font-semibold ${cur === v ? "bg-text text-bg" : "text-muted hover:text-text"}`}>
      {label}</button>
  );
  return (
    <div className="mt-4">
      <div className="text-[14px] text-muted mb-1.5">When a vendor's phone says the bank change wasn't them</div>
      <div className="flex gap-1 rounded-md border-2 border-line p-1">
        {opt("pay_on_file", "Pay the account on file")}{opt("hold", "Hold for me")}
      </div>
    </div>
  );
}

/* Corvid is a separate company with its own ledger; this is its view of the same money. */
function VendorBooksView() {
  const b = usePoll<VendorBooks>(api.corvid);
  const inv = b?.invoices ?? [];
  if (inv.length === 0) return null;
  const tone: Record<string, string> = { paid: "text-ok", void: "text-muted line-through", sent: "text-held" };
  const latest = (b?.events ?? []).find((e) => !e.event.startsWith("handled") && !e.event.startsWith("replied"));
  return (
    <div className="border-t-2 border-line pt-5">
      <h2 className="text-[19px] font-bold">Corvid's books</h2>
      <p className="text-[13px] text-muted mb-3">The vendor's own agent and ledger. Separate company, separate database.</p>
      <ol className="space-y-2">
        {inv.map((i) => (
          <li key={i.number} className={`flex items-baseline justify-between gap-3 ${isFresh(i.updated_at) ? "fresh" : ""}`}>
            <div className="min-w-0">
              <div className={`num text-[15px] font-semibold ${tone[i.status] ?? ""}`}>{i.number}</div>
              <div className="text-[12px] text-muted truncate">{i.status === "paid" ? `paid · ${i.transfer_id ?? ""}` : i.status === "void" ? i.note : "awaiting payment"}</div>
            </div>
            <div className={`num text-[16px] font-bold ${tone[i.status] ?? ""}`}>{usd(i.amount_cents)}</div>
          </li>
        ))}
      </ol>
      {latest && <p className="text-[13px] text-muted mt-3">Latest: {latest.event}</p>}
    </div>
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
