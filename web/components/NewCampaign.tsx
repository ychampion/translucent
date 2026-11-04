"use client";
// New engineering campaign (/new/): one form in six numbered parts, every choice filled from the server.
//   1 system and ledger, 2 dataset and its baseline record, 3 objective and constraints (from the system's
//   objective template, units shown), 4 planner (Bayesian optimisation by default; a paid model only by explicit
//   choice, with its readiness), 5 budgets and approval policy, 6 review and launch. Launch opens the workspace.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, newIdempotencyKey, v1 } from "@/lib/api";
import { loopHref, unitWord, withUnit, type LedgerInfo } from "@/lib/loop";
import {
  defaultPlanner, draftFor, draftProblems, FALLBACK_PLANNERS, FASTEST_STATEMENT, fileNameProblem, limitWords, newUploadId, openBody, plannerGate, plannerIdentity,
  quantityLabel, RECORDS_MAX_BYTES, RECORDS_MAX_DOC, RECORDS_MAX_FILES, RECORDS_SYSTEM, recordsProblems, scalarQuantities, templateOf,
  type DatasetDetail, type DatasetSummary, type Draft, type DraftConstraint, type PlannerInfo, type SystemSpecFull,
} from "@/lib/newcampaign";
import { canWrite, session, useApiConfigured, useSession } from "@/lib/session";
import { apiConfigured } from "@/lib/sources";
import { IconPlus, IconClose, IconUpload, Spinner } from "./Icons";
import { ApiProblem } from "./Session";
import { PageHeader } from "./Shell";

const EMPTY: Draft = {
  title: "", system: "", dataset: "", baseline: "", baselineSimulation: true,
  target: { quantity: "", unit: "", direction: "minimize", label: "" }, mode: "fastest_feasible", hardLimit: "", constraints: [],
  planner: "", experiments: "8", modelUsd: "", autoApprove: false, autoMaxWall: "300", autoMaxCount: "8", memory: "on",
};

const usd = (n: number | undefined | null) => (n == null ? "–" : `$${n.toFixed(2)}`);
const mib = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KiB` : `${(n / (1024 * 1024)).toFixed(1)} MiB`);
/** The chosen files as uploaded once: the same files again reuse that upload instead of sending them twice. */
const filesKey = (fs: File[]) => fs.map((f) => `${f.name}:${f.size}:${f.lastModified}`).join("|");

export function NewCampaign() {
  const router = useRouter();
  const s = useSession();
  const configured = useApiConfigured();
  useEffect(() => { document.title = "New campaign – Translucent"; }, []);

  const [ledgers, setLedgers] = useState<LedgerInfo[] | null>(null);
  const [lid, setLid] = useState("");
  const [specs, setSpecs] = useState<SystemSpecFull[] | null>(null);
  const [datasets, setDatasets] = useState<DatasetSummary[] | null>(null);
  const [planners, setPlanners] = useState<PlannerInfo[] | null>(null);
  const [plannersListed, setPlannersListed] = useState(true);
  const [detail, setDetail] = useState<DatasetDetail | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const [d, setD] = useState<Draft>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<{ text: string; reasons: string[] } | null>(null);
  const idem = useRef<{ body: string; key: string } | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [sending, setSending] = useState<{ n: number; of: number; name: string } | null>(null);
  const uploaded = useRef<{ files: string; id: string } | null>(null);
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }));

  // 1: ledgers, then the ledger's systems, datasets and planners.
  useEffect(() => {
    if (!apiConfigured()) return;
    const ctl = new AbortController();
    setLoadError(null);
    v1.ledgers(ctl.signal).then((ls) => {
      setLedgers(ls);
      const asked = new URLSearchParams(window.location.search).get("ledger");
      setLid((cur) => cur || (ls.find((l) => l.id === asked) ?? ls[0])?.id || "");
    }).catch((e) => { if (!ctl.signal.aborted) setLoadError(e); });
    return () => ctl.abort();
  }, [s.epoch, attempt]);

  useEffect(() => {
    if (!lid) return;
    const ctl = new AbortController();
    setSpecs(null); setDatasets(null); setPlanners(null);
    Promise.all([
      v1.systemSpecs(lid, ctl.signal),
      v1.datasets(lid, ctl.signal),
      v1.planners(lid, ctl.signal).then((p) => { setPlannersListed(true); return p; }).catch((e) => {
        // An older server has no planners endpoint: offer only the free planner it opens, and say so.
        if (e instanceof ApiError && e.status === 404) { setPlannersListed(false); return FALLBACK_PLANNERS; }
        throw e;
      }),
    ]).then(([sp, ds, pl]) => {
      setSpecs(sp); setDatasets(ds); setPlanners(pl);
      const withData = sp.find((x) => ds.some((y) => y.system === x.id)) ?? sp[0];
      const firstDs = ds.find((y) => y.system === withData?.id);
      const dp = defaultPlanner(pl);
      // a reload after sign-in keeps what the person already chose, while the server still offers it
      setD((x) => sp.some((y) => y.id === x.system) && (x.source === "records" || ds.some((y) => y.id === x.dataset))
        ? { ...x, planner: pl.some((y) => y.id === x.planner) ? x.planner : dp?.id ?? "" }
        : { ...x, system: withData?.id ?? "", dataset: firstDs?.id ?? "", planner: dp?.id ?? "",
          title: x.title || (withData?.title ? `${withData.title.split(",")[0]}: fastest feasible protocol` : "") });
    }).catch((e) => { if (!ctl.signal.aborted) setLoadError(e); });
    return () => ctl.abort();
  }, [lid, s.epoch]);

  const spec = specs?.find((x) => x.id === d.system) ?? null;
  const template = templateOf(spec);
  const planner = planners?.find((p) => p.id === d.planner) ?? null;

  // 2: the dataset's records; a new system or dataset refills the objective from the template.
  useEffect(() => {
    if (!lid || !d.dataset) { setDetail(null); return; }
    const ctl = new AbortController();
    v1.dataset(lid, d.dataset, ctl.signal).then(setDetail).catch((e) => { if (!ctl.signal.aborted) setLoadError(e); });
    return () => ctl.abort();
  }, [lid, d.dataset]);
  const filled = useRef("");   // the system and dataset the objective was last filled for: a reload after sign-in keeps edits
  useEffect(() => {
    if (!spec) return;
    const ds = detail && detail.system === spec.id ? detail : null;
    const key = `${lid}|${spec.id}|${ds?.id ?? ""}`;
    if (filled.current === key) return;
    filled.current = key;
    const fill = draftFor(spec, ds);
    setD((x) => ({ ...x, ...fill, dataset: x.dataset, autoMaxCount: fill.experiments }));
  }, [spec?.id, detail?.id]);   // eslint-disable-line react-hooks/exhaustive-deps

  const fromRecords = d.source === "records";
  const recordsSpec = specs?.find((x) => x.id === RECORDS_SYSTEM) ?? null;
  const problems = useMemo(() => [
    ...draftProblems(d, planner, { canWrite: canWrite(s.who), role: s.who?.role }),
    ...(d.source === "records" ? recordsProblems(files, d.documentation ?? "") : []),
  ], [d, planner, s.who, files]);

  // Records: every file goes to one fresh upload id first, then the campaign opens naming it; the server lists them.
  const sendRecords = async (): Promise<string> => {
    const key = filesKey(files);
    if (uploaded.current?.files === key) return uploaded.current.id;
    const id = newUploadId();
    for (const [i, f] of files.entries()) {
      setSending({ n: i + 1, of: files.length, name: f.name });
      try {
        await v1.upload(lid, id, f.name, f);
      } catch (err) {
        if (err instanceof ApiError) throw new ApiError(err.status, `Couldn’t upload ${f.name}: ${err.message}`, err.detail);
        throw err;
      }
    }
    uploaded.current = { files: key, id };
    return id;
  };

  const launch = async () => {
    if (!spec || !planner || problems.length) return;
    setBusy(true);
    setRefusal(null);
    let step: "upload" | "open" = "upload";
    try {
      const body = { ...openBody(d, spec, planner), ...(fromRecords ? { records_upload_id: await sendRecords() } : {}) };
      setSending(null);
      step = "open";
      const text = JSON.stringify(body);
      if (idem.current?.body !== text) idem.current = { body: text, key: newIdempotencyKey() };
      const r = await v1.open(lid, body, idem.current.key);
      router.push(loopHref(lid, r.campaign.campaign_id));
    } catch (err) {
      const e = err instanceof ApiError ? err : null;
      if (e?.status === 404 && step === "open") uploaded.current = null;   // the upload expired or was used: send the files again
      setSending(null);
      setRefusal(!e ? { text: step === "upload" ? "Couldn’t upload the records." : "Couldn’t open the campaign.", reasons: [] }
        : e.status === 401 ? { text: "Your session ended. Sign in, then launch again; the form is kept.", reasons: [] }
          : e.status === 403 ? { text: `Refused: ${e.message}`, reasons: [] }
            : step === "upload" ? { text: e.message, reasons: [] }
              : e.status === 422 ? { text: fromRecords ? "The server refused the campaign:" : "The server refused the objective:", reasons: e.reasons }
                : e.unreachable ? { text: "The team server didn’t answer. Nothing was opened; try again.", reasons: [] }
                  : { text: e.message, reasons: [] });
      setBusy(false);
    }
  };

  if (!configured) {
    return <div className="screen"><PageHeader title="New campaign" /><main className="ws-form-main"><p className="lp-plain">A new campaign needs the team server (NEXT_PUBLIC_API_BASE). The <Link className="text-link" href="/w/?recording=fastcharge">recorded example</Link> works without one.</p></main></div>;
  }
  if (loadError && !ledgers) {
    return <div className="screen"><PageHeader title="New campaign" /><ApiProblem error={loadError} what="the server’s ledgers" onRetry={() => setAttempt((n) => n + 1)} /></div>;
  }

  const records = detail?.records ?? [];
  const baselines = records.filter((r) => r.role === "baseline");
  const scalars = scalarQuantities(spec);
  const unitOf = (q: string) => spec?.quantities.find((x) => x.name === q)?.unit ?? "";
  const datasetsHere = (datasets ?? []).filter((x) => x.system === d.system);

  return (
    <div className="screen">
      <PageHeader title="New campaign" />
      <main className="ws-form-main">
        <header className="ws-form-head">
          <h1 className="ws-home-title">New engineering campaign</h1>
          <p className="ws-home-lead">Every field is filled from the server. Nothing runs until a person approves its request.</p>
          {s.who && !canWrite(s.who) && <p className="lp-error" role="note">You are signed in as a viewer: you can read this form but not launch it.</p>}
          {s.status === "out" && <p className="ws-note" role="note">Sign in to load the server’s systems. <button type="button" className="text-link" onClick={() => session.ask()}>Sign in</button></p>}
        </header>
        {loadError ? <ApiProblem error={loadError} what="this ledger’s systems, datasets or planners" onRetry={() => setAttempt((n) => n + 1)} /> : null}

        <form className="ws-form" onSubmit={(e) => { e.preventDefault(); launch(); }}>
          <Part n={1} title="System and ledger">
            <div className="ws-grid2">
              <Field label="Ledger" id="f-ledger">
                <select id="f-ledger" className="field" value={lid} onChange={(e) => { setLid(e.target.value); setD(EMPTY); }}>
                  {(ledgers ?? []).map((l) => <option key={l.id} value={l.id}>{l.title || l.id} ({l.id})</option>)}
                </select>
              </Field>
              <Field label="System" id="f-system">
                <select id="f-system" className="field" value={d.system} onChange={(e) => {
                  const sys = e.target.value;
                  set({ system: sys, dataset: (datasets ?? []).find((x) => x.system === sys)?.id ?? "", ...(sys !== RECORDS_SYSTEM ? { source: "dataset" as const } : {}) });
                }}>
                  {(specs ?? []).map((x) => <option key={x.id} value={x.id}>{x.title || x.id}</option>)}
                </select>
              </Field>
            </div>
            {spec && <p className="ws-hint">{spec.description}{spec.solver ? ` · solver ${spec.solver.name} ${spec.solver.version}` : " · no solver: results come from people"}{template ? "" : " · this server serves no objective template for it, so set the target and constraints yourself"}</p>}
            {!specs && lid && !loadError && <p className="ws-hint"><Spinner size={14} /> Loading the ledger…</p>}
          </Part>

          <Part n={2} title={fromRecords ? "Your records" : "Dataset and baseline"}>
            {recordsSpec && (
              <fieldset className="ws-modes">
                <legend className="visually-hidden">Where the evidence starts</legend>
                <label className={`ws-mode ${!fromRecords ? "is-on" : ""}`}>
                  <input type="radio" name="source" checked={!fromRecords} onChange={() => set({ source: "dataset", dataset: datasetsHere[0]?.id ?? d.dataset })} />
                  <span><strong>A dataset in the ledger</strong><br /><span className="ws-hint">Records already imported, with a baseline record.</span></span>
                </label>
                <label className={`ws-mode ${fromRecords ? "is-on" : ""}`}>
                  <input type="radio" name="source" checked={fromRecords} onChange={() => set({ source: "records", system: RECORDS_SYSTEM, dataset: "" })} />
                  <span><strong>From your records</strong><br /><span className="ws-hint">Raw exports as they are, and a few lines on what they are. The agent reads them and writes the import itself. {recordsSpec.title ? `System: ${recordsSpec.title}.` : ""}</span></span>
                </label>
              </fieldset>
            )}
            {fromRecords ? <RecordsInput files={files} setFiles={setFiles} documentation={d.documentation ?? ""} setDocumentation={(t) => set({ documentation: t })} /> : (
            <>
            {datasetsHere.length === 0 && specs ? <p className="lp-plain">No dataset for this system in the ledger. Import one with <code>translucent import</code>{recordsSpec ? ", or start from your records" : ""}.</p> : (
              <Field label="Dataset" id="f-dataset">
                <select id="f-dataset" className="field" value={d.dataset} onChange={(e) => set({ dataset: e.target.value })}>
                  {datasetsHere.map((x) => <option key={x.id} value={x.id}>{x.title} ({x.records} records)</option>)}
                </select>
              </Field>
            )}
            {detail && (
              <>
              {detail.citation ? <p className="ws-hint">{detail.citation}</p> : null}
              <div className="ws-table-wrap">
                <table className="ws-table">
                  <caption className="ws-caption">{detail.title}</caption>
                  <thead><tr><th scope="col">Record</th><th scope="col">Role</th><th scope="col">Configuration</th><th scope="col">Channels (unit)</th></tr></thead>
                  <tbody>
                    {records.map((r) => (
                      <tr key={r.id} className={r.role === "baseline" ? "is-baseline" : ""}>
                        <th scope="row">
                          {r.role === "baseline" && baselines.length > 1 ? (
                            <label className="ws-inline"><input type="radio" name="baseline" checked={d.baseline === r.id} onChange={() => set({ baseline: r.id })} /> {r.id}</label>
                          ) : r.id}
                        </th>
                        <td>{r.role === "baseline" ? <span className="ws-chip is-base">Baseline</span> : r.role}</td>
                        <td className="num">{Object.entries(r.configuration ?? {}).map(([k, val]) => `${k} ${typeof val === "number" ? withUnit(val, r.configuration_units?.[k] ?? "") : String(val)}`).join(", ") || "–"}</td>
                        <td>{r.channels.length ? r.channels.map((c) => `${c.quantity} (${unitWord(c.unit) || "–"})`).join(", ") : <span className="ws-muted">none: {r.provenance?.notes ? r.provenance.notes : "no measurement"}</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              </>
            )}
            {spec?.solver && (
              <label className="ws-check"><input type="checkbox" checked={d.baselineSimulation} onChange={(e) => set({ baselineSimulation: e.target.checked })} />
                Re-run the baseline’s configuration on the solver at registration (a computational baseline)</label>
            )}
            </>
            )}
          </Part>

          <Part n={3} title="Objective and constraints">
            <fieldset className="ws-modes">
              <legend className="visually-hidden">What counts as done</legend>
              <label className={`ws-mode ${d.mode === "fastest_feasible" ? "is-on" : ""}`}>
                <input type="radio" name="mode" checked={d.mode === "fastest_feasible"} onChange={() => set({ mode: "fastest_feasible" })} />
                <span><strong>Fastest meeting the constraints</strong> (default)<br /><span className="ws-hint">“{FASTEST_STATEMENT}” Stays open: the best result is the best found, not a proven optimum.</span></span>
              </label>
              <label className={`ws-mode ${d.mode === "hard_target" ? "is-on" : ""}`}>
                <input type="radio" name="mode" checked={d.mode === "hard_target"} onChange={() => set({ mode: "hard_target" })} />
                <span><strong>Hard target</strong><br /><span className="ws-hint">Met or not met. Nothing relaxes it: only an engineer’s recorded amendment changes it.</span></span>
              </label>
            </fieldset>
            <div className="ws-grid3">
              <Field label="Target quantity" id="f-target">
                <select id="f-target" className="field" value={d.target.quantity} onChange={(e) => {
                  const q = e.target.value;
                  set({ target: { ...d.target, quantity: q, unit: unitOf(q), label: quantityLabel(spec, q) } });
                }}>
                  {scalars.map((q) => <option key={q.name} value={q.name}>{quantityLabel(spec, q.name)} ({unitWord(q.unit) || "–"})</option>)}
                </select>
              </Field>
              <Field label="Direction" id="f-dir">
                <select id="f-dir" className="field" value={d.target.direction} onChange={(e) => set({ target: { ...d.target, direction: e.target.value as "minimize" | "maximize" } })}>
                  <option value="minimize">Lower is better</option><option value="maximize">Higher is better</option>
                </select>
              </Field>
              {d.mode === "hard_target" && (
                <Field label={`Hard target (${d.target.direction === "maximize" ? "at least" : "at most"}, ${unitWord(d.target.unit) || "–"})`} id="f-hard">
                  <input id="f-hard" className="field num" inputMode="decimal" value={d.hardLimit} onChange={(e) => set({ hardLimit: e.target.value.replace(/[^\d.-]/g, "") })} />
                </Field>
              )}
            </div>
            <p className="ws-sub">Constraints every candidate must meet</p>
            <ul className="ws-cons">
              {d.constraints.map((c, i) => (
                <ConstraintRow key={i} c={c} spec={spec} onChange={(x) => set({ constraints: d.constraints.map((y, j) => (j === i ? x : y)) })}
                  onRemove={() => set({ constraints: d.constraints.filter((_, j) => j !== i) })} />
              ))}
            </ul>
            <button type="button" className="chip-btn" onClick={() => {
              const q = scalars.find((x) => x.name !== d.target.quantity && !d.constraints.some((c) => c.quantity === x.name));
              if (q) set({ constraints: [...d.constraints, { quantity: q.name, op: "<=", limit: "", unit: q.unit, label: "" }] });
            }}><IconPlus size={14} />Add a constraint</button>
            {template?.reference_search ? <p className="ws-hint">Speed is compared only with a reference that meets the same constraints: the baseline if it does, else the best found by the system’s reference search ({template.reference_search.label ?? "reference search"}{template.reference_search.method ? `: ${template.reference_search.method}` : ""}). It is not counted against the experiment budget.</p> : null}
            {template?.model_uncertainty ? <p className="ws-hint">Model uncertainty, in the system’s words: {template.model_uncertainty}</p> : null}
          </Part>

          <Part n={4} title="Planner">
            {!plannersListed && <p className="ws-note" role="note">This server doesn’t report its planners’ readiness yet, so only the free Bayesian-optimisation planner is offered.</p>}
            <div className="ws-planners" role="radiogroup" aria-label="Planner">
              {(planners ?? []).map((p) => {
                const gate = plannerGate(p);
                return (
                  <label key={p.id} className={`ws-planner ${d.planner === p.id ? "is-on" : ""} ${gate.ok ? "" : "is-off"}`}>
                    <input type="radio" name="planner" value={p.id} checked={d.planner === p.id} disabled={!gate.ok}
                      onChange={() => set({ planner: p.id, modelUsd: p.paid ? String(p.default_budget_usd ?? "") : "" })} />
                    <span className="ws-planner-body">
                      <span className="ws-planner-top">
                        <strong>{p.label}</strong>
                        <span className={`ws-chip ${p.paid ? "is-paid" : "is-free"}`}>{p.paid ? "Paid" : "Free"}</span>
                        <span className={`ws-chip ${p.ready ? "is-ready" : "is-notready"}`}>{p.ready ? "Ready" : "Not ready"}</span>
                        {p.id === "bo" && <span className="ws-chip">Default</span>}
                      </span>
                      <span className="ws-hint">{plannerIdentity(p)}</span>
                      {p.model && <span className="ws-hint">Model the server will call: <code>{p.model}</code>{p.profile ? ` · profile ${p.profile}` : ""}</span>}
                      {p.paid && (p.providers ?? [{ provider: p.profile ?? "", cap_usd: p.cap_usd ?? 0, spent_usd: p.spent_usd ?? 0, remaining_usd: p.remaining_usd ?? 0 }]).map((q) => (
                        <span key={q.provider} className="ws-hint num">{q.provider} cap {usd(q.cap_usd)} · spent {usd(q.spent_usd)} · remaining {usd(q.remaining_usd)}</span>
                      ))}
                      {!gate.ok && <span className="ws-reason" role="note">Can’t launch: {gate.reason}</span>}
                    </span>
                  </label>
                );
              })}
            </div>
          </Part>

          <Part n={5} title="Budgets and approval">
            <div className="ws-grid3">
              <Field label="Experiments (most it may run)" id="f-exp">
                <input id="f-exp" className="field num" inputMode="numeric" value={d.experiments} onChange={(e) => set({ experiments: e.target.value.replace(/\D/g, "") })} />
              </Field>
              {planner?.paid && (
                <Field label="Model budget (USD)" id="f-usd">
                  <input id="f-usd" className="field num" inputMode="decimal" value={d.modelUsd} onChange={(e) => set({ modelUsd: e.target.value.replace(/[^\d.]/g, "") })} />
                </Field>
              )}
            </div>
            <p className="ws-sub">Approval policy</p>
            <p className="lp-plain">Every request waits for a person’s approval of its exact version.</p>
            <label className="ws-check"><input type="checkbox" checked={d.autoApprove} onChange={(e) => set({ autoApprove: e.target.checked })} />
              Auto-approve computational runs under these limits (off by default)</label>
            {d.autoApprove && (
              <div className="ws-grid3">
                <Field label="Longest run (s)" id="f-wall"><input id="f-wall" className="field num" inputMode="decimal" value={d.autoMaxWall} onChange={(e) => set({ autoMaxWall: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
                <Field label="At most this many" id="f-count"><input id="f-count" className="field num" inputMode="numeric" value={d.autoMaxCount} onChange={(e) => set({ autoMaxCount: e.target.value.replace(/\D/g, "") })} /></Field>
                <p className="ws-hint">Cost per run: $0 (computational only). Physical tests always wait for a person.</p>
              </div>
            )}
          </Part>

          <Part n={6} title="Review and launch">
            <Field label="Title" id="f-title">
              <input id="f-title" className="field" value={d.title} maxLength={200} onChange={(e) => set({ title: e.target.value })} />
            </Field>
            <dl className="ws-review">
              <div><dt>System</dt><dd>{spec?.title ?? "–"}</dd></div>
              {fromRecords
                ? <div><dt>Records</dt><dd>{files.length ? `${files.length} file${files.length === 1 ? "" : "s"}, ${mib(files.reduce((n, f) => n + f.size, 0))}; the agent imports them and lists what it could not establish` : "–"}</dd></div>
                : <div><dt>Baseline</dt><dd>{d.dataset && d.baseline ? `${d.dataset}/${d.baseline}` : "–"}{d.baselineSimulation && spec?.solver ? ", re-run on the solver" : ""}</dd></div>}
              <div><dt>Objective</dt><dd>{d.mode === "hard_target"
                ? `Hard target: ${d.target.label || d.target.quantity} ${d.target.direction === "maximize" ? "≥" : "≤"} ${d.hardLimit ? withUnit(Number(d.hardLimit), d.target.unit) : "–"}`
                : `${FASTEST_STATEMENT} (${d.target.label || d.target.quantity}, ${d.target.direction === "maximize" ? "higher" : "lower"} is better)`}</dd></div>
              <div><dt>Constraints</dt><dd>{d.constraints.length ? d.constraints.map((c) => `${quantityLabel(spec, c.quantity)} ${limitWords(c.op, c.limit, c.unit)}`).join("; ") : "none"}</dd></div>
              <div><dt>Planner</dt><dd>{planner ? plannerIdentity(planner) : "–"}</dd></div>
              <div><dt>Budget</dt><dd>{d.experiments || "–"} experiments{planner?.paid ? `, model ${d.modelUsd ? `$${d.modelUsd}` : "–"}` : ", no model spend"}</dd></div>
              <div><dt>Approval</dt><dd>{d.autoApprove ? `Auto-approve up to ${d.autoMaxCount} computational runs of at most ${d.autoMaxWall} s; others wait for a person` : "Every request waits for a person"}</dd></div>
            </dl>
            {problems.length > 0 && <ul className="ws-problems" aria-label="Before it can launch">{problems.map((p) => <li key={p}>{p}</li>)}</ul>}
            {refusal && (
              <div className="lp-error ws-refusal" role="alert">
                <p>{refusal.text}</p>
                {refusal.reasons.length > 0 && <ul>{refusal.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
              </div>
            )}
            <div className="ws-launch">
              <button type="submit" className="btn" disabled={busy || problems.length > 0}>{busy ? <Spinner size={14} /> : null}{sending ? `Uploading ${sending.n} of ${sending.of}: ${sending.name}` : fromRecords ? "Upload and launch" : "Launch campaign"}</button>
              <Link href="/" className="btn btn-quiet">Cancel</Link>
            </div>
          </Part>
        </form>
      </main>
    </div>
  );
}

function Part({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="ws-part" aria-labelledby={`part-${n}`}>
      <h2 className="ws-part-h" id={`part-${n}`}><span className="ws-part-n num" aria-hidden>{n}</span>{title}</h2>
      <div className="ws-part-body">{children}</div>
    </section>
  );
}

function Field({ label, id, children }: { label: string; id: string; children: React.ReactNode }) {
  return <div className="ws-field"><label className="ws-label" htmlFor={id}>{label}</label>{children}</div>;
}

/** The engineer's raw files (each checked here against the upload rules, before anything is sent) and their documentation. */
function RecordsInput({ files, setFiles, documentation, setDocumentation }: {
  files: File[]; setFiles(f: File[]): void; documentation: string; setDocumentation(t: string): void;
}) {
  const add = (list: FileList | null) => {
    const picked = Array.from(list ?? []);
    setFiles([...files.filter((f) => !picked.some((p) => p.name === f.name)), ...picked]);
  };
  return (
    <>
      <label className={`file-pick ${files.length ? "has-file" : ""}`}>
        <IconUpload size={18} />
        <span className="file-pick-label">{files.length ? "Add more record files" : "Choose the record files"}</span>
        <span className="file-pick-hint">1 to {RECORDS_MAX_FILES}, each at most {RECORDS_MAX_BYTES / (1024 * 1024)} MiB</span>
        <input id="f-files" type="file" multiple onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
      </label>
      <p className="ws-hint">Exports as they come off the tester or logger (CSV or text). Names: letters, digits, dot, dash, underscore; no spaces.</p>
      {files.length > 0 && (
        <ul className="ws-files" aria-label="Chosen files">
          {files.map((f) => {
            const problem = fileNameProblem(f.name) ?? (f.size > RECORDS_MAX_BYTES ? `larger than ${RECORDS_MAX_BYTES / (1024 * 1024)} MiB` : null);
            return (
              <li key={f.name} className={problem ? "is-bad" : ""}>
                <code className="ws-wrap">{f.name}</code>
                <span className="ws-muted num">{mib(f.size)}</span>
                <button type="button" className="icon-btn" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((x) => x !== f))}><IconClose size={14} /></button>
                {problem && <span className="ws-reason">{problem.startsWith(`${f.name}: `) ? problem.slice(f.name.length + 2) : problem}</span>}
              </li>
            );
          })}
        </ul>
      )}
      <Field label="What the files are" id="f-doc">
        <textarea id="f-doc" className="field ws-textarea" rows={6} value={documentation} maxLength={RECORDS_MAX_DOC}
          placeholder="Tester and export, one file per cell; columns and their units (and sign conventions); where the thermocouple sits; the chamber temperature; what each test did."
          onChange={(e) => setDocumentation(e.target.value)} />
      </Field>
      <p className="ws-hint num">{documentation.length} of {RECORDS_MAX_DOC} characters. The agent reads this as written. What it cannot establish from the files and this text, it lists as missing information.</p>
    </>
  );
}

function ConstraintRow({ c, spec, onChange, onRemove }: { c: DraftConstraint; spec: SystemSpecFull | null; onChange(c: DraftConstraint): void; onRemove(): void }) {
  const scalars = scalarQuantities(spec);
  const changed = c.templateLimit != null && c.limit !== "" && Number(c.limit) !== c.templateLimit;
  return (
    <li className="ws-con">
      <select className="field" aria-label="Quantity" value={c.quantity} onChange={(e) => {
        const q = scalars.find((x) => x.name === e.target.value);
        onChange({ quantity: e.target.value, op: c.op, limit: c.limit, unit: q?.unit ?? "", label: "" });
      }}>
        {scalars.map((q) => <option key={q.name} value={q.name}>{quantityLabel(spec, q.name)}</option>)}
      </select>
      <select className="field ws-op" aria-label="Relation" value={c.op} onChange={(e) => onChange({ ...c, op: e.target.value as "<=" | ">=" })}>
        <option value="<=">≤ at most</option><option value=">=">≥ at least</option>
      </select>
      <span className="ws-limit">
        <input className="field num" aria-label={`Limit in ${unitWord(c.unit) || "no unit"}`} inputMode="decimal" value={c.limit} onChange={(e) => onChange({ ...c, limit: e.target.value.replace(/[^\d.-]/g, "") })} />
        <span className="ws-unit">{unitWord(c.unit) || "–"}</span>
      </span>
      <button type="button" className="icon-btn" aria-label="Remove this constraint" onClick={onRemove}><IconClose size={16} /></button>
      {changed && <span className="ws-hint ws-con-note">Template value {c.templateLimit} {unitWord(c.unit)}</span>}
    </li>
  );
}
