import { useEffect, useMemo, useRef, useState } from "react";
import { usd } from "./api";

type Details = {
  id: string; status: string; vendor: string; old_last4: string; new_last4: string | null;
  registry_version: number; base_registry_version: number; evidence: Record<string, string>;
  flags: string[]; invoice_cents: number | null; po: string | null; case_id: string | null; undo_window_seconds: number;
  authorization: { id: string; status: string; release_at: string | null; needs_reconfirm: boolean;
                   stripe_transfer_id: string | null } | null;
};

const FLAG_TEXT: Record<string, string> = {
  REMIT_TO_CHANGED: "The request came by email and names a different account than the one on file.",
  BANK_CHANGE_LANGUAGE: "The email uses bank-change wording, a common invoice-fraud pattern.",
  EMBEDDED_INSTRUCTIONS_IN_ATTACHMENT: "An attachment contained hidden instructions aimed at an automated payer.",
  URGENCY_LANGUAGE: "The email pressures for urgent payment.",
  REPLY_TO_MISMATCH: "Replies would go to a different domain than the sender's.",
};

export default function Approve() {
  const id = location.pathname.split("/")[2];
  const params = new URLSearchParams(location.search);
  const auth = useMemo(() => ({ exp: Number(params.get("exp")), t: params.get("t") ?? "" }), []); // eslint-disable-line
  const opened = useRef(Date.now());
  const [d, setD] = useState<Details | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [passcode, setPasscode] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    let alive = true;
    const load = () => fetch(`/api/approvals/${id}?exp=${auth.exp}&t=${auth.t}`)
      .then(async (r) => { if (!r.ok) throw new Error(r.status === 403 ? "This approval link is invalid or has expired." : "Couldn't load this request."); return r.json(); })
      .then((j) => alive && setD(j)).catch((e) => alive && setErr(e.message));
    load();
    const t = setInterval(() => { load(); setNow(Date.now()); }, 1000);
    return () => { alive = false; clearInterval(t); };
  }, [id, auth]);

  const post = async (path: string, body: object) => {
    setBusy(true); setErr(null);
    const r = await fetch(`/api/approvals/${id}/${path}`, { method: "POST", headers: { "Content-Type": "application/json" },
                                                           body: JSON.stringify({ ...auth, ...body }) });
    const j = await r.json().catch(() => ({}));
    setBusy(false);
    if (!r.ok || j.ok === false) setErr(j.detail || j.reason || `Request failed (${r.status})`);
  };

  if (err && !d) return <Shell><p className="text-bad text-[20px] font-semibold">{err}</p></Shell>;
  if (!d) return <Shell><p className="text-muted text-[18px]">Loading request…</p></Shell>;

  const a = d.authorization;
  const secondsLeft = a?.release_at ? Math.max(0, Math.ceil((new Date(a.release_at).getTime() - now) / 1000)) : 0;
  const scheduled = a?.status === "scheduled";
  const STATUS_WORDS: Record<string, string> = {
    pending_verification: "Waiting for vendor confirmation", verified: "Vendor confirmed; needs your decision",
    approved: "Approved", rejected: "Rejected", superseded: "Superseded (vendor records changed; start over)",
    unresolved: "No clear answer from the vendor; call them yourself",
  };

  return (
    <Shell>
      <p className="text-muted text-[17px] mb-1">Bank change request</p>
      <h1 className="wide text-[38px] font-extrabold leading-tight mb-6">{d.vendor}</h1>

      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-4 rounded-lg border-2 border-line bg-panel p-6 mb-6">
        <div><p className="text-muted text-[15px]">Account on file</p><p className="num text-[32px] font-bold text-ok">••{d.old_last4}</p></div>
        <p className="text-[28px] text-muted">to</p>
        <div><p className="text-muted text-[15px]">Requested by email</p><p className="num text-[32px] font-bold text-bad">••{d.new_last4 ?? "????"}</p></div>
      </div>

      <dl className="grid grid-cols-[200px_1fr] gap-y-3 text-[17px] mb-6">
        <dt className="text-muted">Held invoice</dt><dd className="num">{d.invoice_cents ? usd(d.invoice_cents) : "-"} {d.po}</dd>
        <dt className="text-muted">Vendor confirmation</dt>
        <dd>{d.status === "pending_verification" ? <span className="text-held font-semibold">Waiting for the vendor to answer on the phone on file</span>
             : <>Confirmed by {d.evidence.channel ?? "phone"} from the phone on file (ending {(d.evidence.phone_on_file ?? "").slice(-4)})</>}</dd>
        <dt className="text-muted">Registry version</dt><dd className="num">v{d.registry_version}</dd>
        <dt className="text-muted">Request status</dt><dd>{STATUS_WORDS[d.status] ?? d.status}</dd>
      </dl>

      {d.status === "verified" && (
        <section className="rounded-lg border-2 border-held p-6 mb-6">
          <h2 className="text-[20px] font-bold mb-3">Before you approve</h2>
          <ul className="space-y-2 mb-4 text-[17px]">
            {d.flags.map((f) => <li key={f} className="text-held">{FLAG_TEXT[f] ?? f}</li>)}
          </ul>
          <label className="flex items-start gap-3 text-[17px] mb-4">
            <input type="checkbox" className="mt-1.5 h-5 w-5" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            I've read these warnings and confirmed the change with {d.vendor} myself.
          </label>
          <label className="block text-[17px] mb-2" htmlFor="last4">Type the last 4 digits of the new account</label>
          <input id="last4" value={typed} onChange={(e) => setTyped(e.target.value.replace(/\D/g, "").slice(0, 4))}
                 className="num text-[28px] w-40 px-3 py-2 rounded-md border-2 border-line bg-raised mb-5" inputMode="numeric" />
          <label className="block text-[17px] mb-2" htmlFor="passcode">Admin passcode <span className="text-muted text-[14px]">(the link alone can't approve)</span></label>
          <input id="passcode" type="password" autoComplete="off" value={passcode} onChange={(e) => setPasscode(e.target.value)}
                 className="num text-[22px] w-56 px-3 py-2 rounded-md border-2 border-line bg-raised mb-5 block" />
          <div className="flex gap-3">
            <button disabled={busy || typed.length !== 4 || !passcode || (d.flags.length > 0 && !ack)}
              onClick={() => post("approve", { typed_last4: typed, passcode, acknowledged_flags: ack, seconds_on_page: (Date.now() - opened.current) / 1000 })}
              className="px-5 py-2.5 rounded-md text-[18px] font-bold bg-text text-bg disabled:opacity-40">Approve new account</button>
            <button disabled={busy} onClick={() => post("reject", {})}
              className="px-5 py-2.5 rounded-md text-[18px] font-bold border-2 border-line">Reject and pay the account on file</button>
          </div>
        </section>
      )}

      {scheduled && a && (
        <section className="rounded-lg border-2 border-held p-6 mb-6">
          {a.needs_reconfirm ? (
            <>
              <h2 className="text-[22px] font-bold text-held mb-2">This was approved very quickly</h2>
              <p className="text-[17px] mb-4">The request still carries risk flags. Payment won't release until you confirm again.</p>
              <div className="flex gap-3">
                <button disabled={busy} onClick={() => post("reconfirm", { authorization_id: a.id, passcode })}
                  className="px-5 py-2.5 rounded-md text-[18px] font-bold bg-text text-bg">Yes, release payment</button>
                <button disabled={busy} onClick={() => post("cancel", { authorization_id: a.id })}
                  className="px-5 py-2.5 rounded-md text-[18px] font-bold border-2 border-bad text-bad">Cancel payment</button>
              </div>
            </>
          ) : (
            <>
              <p className="text-muted text-[16px]">Payment scheduled. Releases in</p>
              <p className="num text-[56px] font-bold text-held leading-tight mb-3">{secondsLeft}s</p>
              <button disabled={busy} onClick={() => post("cancel", { authorization_id: a.id })}
                className="px-5 py-2.5 rounded-md text-[18px] font-bold border-2 border-bad text-bad">Cancel payment</button>
            </>
          )}
        </section>
      )}

      {a?.status === "succeeded" && <p className="text-ok text-[20px] font-semibold">Paid. Stripe transfer <span className="num">{a.stripe_transfer_id}</span></p>}
      {a?.status === "invalidated" && <p className="text-bad text-[20px] font-semibold">Payment cancelled. The invoice is back on hold.</p>}
      {d.status === "rejected" && <p className="text-ok text-[20px] font-semibold">Change rejected. The invoice pays the account on file.</p>}
      {err && <p className="text-bad text-[17px] mt-4">{err}</p>}
      <p className="text-[13px] text-muted mt-10">This page is the only place a bank change can be approved. Replies to emails or texts never approve anything.</p>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-full bg-bg">
      <header className="px-8 py-4 border-b-2 border-line bg-panel flex items-baseline gap-4">
        <span className="wide text-[26px] font-extrabold">Airlock</span><span className="text-muted text-[16px]">Admin approval</span>
      </header>
      <main className="max-w-[760px] mx-auto px-6 py-10">{children}</main>
    </div>
  );
}
