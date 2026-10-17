import { useEffect, useRef, useState } from "react";
import { api, usd, type CaseDetail, type CaseRow, type Money, type Post } from "./api";

const POLL_MS = 1500;

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

const STATUS: Record<CaseRow["status"], { label: string; cls: string }> = {
  paid: { label: "Paid", cls: "bg-trusted-soft text-trusted" },
  held: { label: "Held", cls: "bg-untrusted-soft text-untrusted" },
  denied: { label: "Denied", cls: "bg-denied-soft text-denied" },
  needs_approval: { label: "Needs you", cls: "bg-untrusted-soft text-untrusted" },
  open: { label: "Checking", cls: "bg-rule/60 text-muted" },
};

export default function App() {
  const cases = usePoll(api.cases) ?? [];
  const money = usePoll(api.money);
  const [picked, setPicked] = useState<string | null>(null);
  const [follow, setFollow] = useState(true);
  const selected = follow ? cases[0]?.id ?? null : picked;

  return (
    <div className="h-full grid grid-rows-[auto_1fr]">
      <Header />
      <main className="grid grid-cols-[minmax(260px,1fr)_minmax(0,2.1fr)_minmax(280px,1.15fr)] min-h-0">
        <Outside cases={cases} selected={selected}
                 onPick={(id) => { setPicked(id); setFollow(false); }}
                 follow={follow} onFollow={() => setFollow(true)} />
        <Chamber caseId={selected} />
        <Inside money={money} />
      </main>
    </div>
  );
}

function Header() {
  return (
    <header className="flex items-baseline justify-between px-8 pt-6 pb-4 border-b border-rule bg-panel">
      <div className="flex items-baseline gap-5">
        <h1 className="wide text-[34px] font-extrabold tracking-tight leading-none">Airlock</h1>
        <p className="text-muted text-[17px]">Email can make claims. It can't move money.</p>
      </div>
      <p className="text-muted text-[15px]">Kiln Supply Co. accounts payable</p>
    </header>
  );
}

/* ---------- Outside: untrusted email arriving ---------- */

function Outside(props: { cases: CaseRow[]; selected: string | null; onPick: (id: string) => void;
                          follow: boolean; onFollow: () => void }) {
  return (
    <section className="min-h-0 flex flex-col border-r-[6px] border-untrusted/70 bg-steel">
      <div className="px-6 pt-5 pb-3 flex items-baseline justify-between">
        <h2 className="text-[19px] font-bold">Outside</h2>
        {!props.follow && (
          <button onClick={props.onFollow} className="text-[14px] text-muted underline underline-offset-4">
            Follow newest
          </button>
        )}
      </div>
      <p className="px-6 pb-3 text-[14px] text-muted">Inbound email. Nothing here is trusted.</p>
      <ol className="overflow-y-auto min-h-0 px-3 pb-6">
        {props.cases.length === 0 && (
          <li className="px-3 py-6 text-muted text-[16px]">Waiting for the first invoice email.</li>
        )}
        {props.cases.map((c) => {
          const s = STATUS[c.status];
          const active = c.id === props.selected;
          return (
            <li key={c.id}>
              <button onClick={() => props.onPick(c.id)}
                className={`w-full text-left px-3 py-3 rounded-md transition-colors ${active ? "bg-panel shadow-[inset_4px_0_0_var(--color-ink)]" : "hover:bg-panel/60"}`}>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[17px] font-semibold truncate">{vendorOf(c)}</span>
                  <span className={`shrink-0 rounded px-2 py-0.5 text-[14px] font-semibold ${s.cls}`}>{s.label}</span>
                </div>
                <div className="text-[14px] text-muted truncate mt-0.5">{invoiceOf(c)} from {c.from_address}</div>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

const vendorOf = (c: CaseRow) => c.subject.replace(/^\[[^\]]+\]\s*/, "").split(":")[0];
const invoiceOf = (c: CaseRow) => c.subject.split(":").slice(1).join(":").trim();

/* ---------- The airlock: the case thread ---------- */

const WHO: Record<string, { name: string; role: string; dot: string }> = {
  intake: { name: "Intake", role: "reads the email, no money access", dot: "bg-untrusted" },
  auditor: { name: "Auditor", role: "Airlock policy, deterministic code", dot: "bg-code" },
  treasurer: { name: "Treasurer", role: "agent holding Airlock's payment tools", dot: "bg-trusted" },
  verifier: { name: "Verifier", role: "calls the phone already on file", dot: "bg-ink" },
  admin: { name: "Admin", role: "human, replied in the thread", dot: "bg-ink" },
  unverified: { name: "Unsigned message", role: "not from an Airlock agent", dot: "bg-denied" },
};

function Chamber({ caseId }: { caseId: string | null }) {
  const detail = usePoll<CaseDetail | null>(() => (caseId ? api.case(caseId) : Promise.resolve(null)), [caseId]);
  const end = useRef<HTMLDivElement>(null);
  const count = detail?.posts.length ?? 0;
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [count, caseId]);

  return (
    <section className="min-h-0 flex flex-col bg-panel">
      <div className="px-8 pt-5 pb-4 border-b border-rule">
        <h2 className="text-[19px] font-bold">The airlock</h2>
        <p className="text-[14px] text-muted">Each invoice is worked in one email thread by the agent team. Claims get checked here before anything moves.</p>
      </div>
      <div className="overflow-y-auto min-h-0 px-8 py-6">
        {!detail && <p className="text-muted text-[17px]">Pick a case on the left, or send an invoice email to watch it arrive.</p>}
        {detail && (
          <>
            <h3 className="condensed text-[26px] font-bold leading-tight mb-1">{invoiceOf(detail.case) || detail.case.subject}</h3>
            <p className="text-[15px] text-muted mb-6">{vendorOf(detail.case)}, case thread {detail.case.id}</p>
            <ol className="space-y-5">
              {detail.posts.map((p, i) => <PostView key={`${detail.case.id}-${i}`} post={p} />)}
            </ol>
          </>
        )}
        <div ref={end} />
      </div>
    </section>
  );
}

function PostView({ post }: { post: Post }) {
  const who = WHO[post.author] ?? { name: post.author, role: "", dot: "bg-muted" };
  const isCode = post.author === "auditor";
  const verdict = isCode ? post.body.split(":")[0] : null;
  const tone = verdict === "APPROVED" ? "border-trusted" : verdict === "DENIED" ? "border-denied"
             : verdict ? "border-untrusted" : post.author === "unverified" ? "border-denied" : "border-rule";
  return (
    <li className={`post-enter border-l-[5px] ${tone} pl-5`}>
      <div className="flex items-center gap-2.5 mb-1.5">
        <span className={`h-2.5 w-2.5 rounded-full ${who.dot}`} />
        <span className="text-[17px] font-bold">{who.name}</span>
        <span className="text-[14px] text-muted">{who.role}</span>
      </div>
      {isCode ? (
        <pre className="font-code text-[15.5px] leading-[1.55] whitespace-pre-wrap text-code">{post.body}</pre>
      ) : (
        <p className="font-letter text-[19px] leading-[1.5] whitespace-pre-wrap max-w-[68ch]">{post.body}</p>
      )}
    </li>
  );
}

/* ---------- Inside: where money actually went ---------- */

function Inside({ money }: { money: Money | null }) {
  const paid = money?.paid_cents ?? 0;
  const stolen = money?.attacker_cents ?? 0;
  const done = (money?.payments ?? []).filter((p) => p.status === "succeeded");
  return (
    <section className="min-h-0 flex flex-col border-l-[6px] border-trusted/70 bg-steel">
      <div className="px-6 pt-5 pb-3">
        <h2 className="text-[19px] font-bold">Inside</h2>
        <p className="text-[14px] text-muted">Money only moves from here, to accounts in the vendor registry.</p>
      </div>
      <div className="px-6 py-4">
        <p className="text-[16px] text-muted">Sent to attackers</p>
        <p className={`wide num font-extrabold leading-none text-[64px] ${stolen === 0 ? "text-trusted" : "text-denied"}`}>
          {usd(stolen)}
        </p>
      </div>
      <div className="px-6 pb-4">
        <p className="text-[16px] text-muted">Paid to real vendors</p>
        <p className="num font-bold text-[30px]">{usd(paid)}</p>
      </div>
      <ol className="overflow-y-auto min-h-0 px-6 pb-6 space-y-3 border-t border-rule pt-4">
        {done.length === 0 && <li className="text-muted text-[15px]">No payments yet.</li>}
        {done.map((p) => (
          <li key={p.id} className="post-enter flex items-baseline justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[16px] font-semibold">{p.vendor}</div>
              <div className="text-[13px] text-muted truncate">Stripe {p.stripe_transfer_id}</div>
            </div>
            <div className="num text-[17px] font-semibold text-trusted">{usd(p.amount_cents)}</div>
          </li>
        ))}
      </ol>
    </section>
  );
}
