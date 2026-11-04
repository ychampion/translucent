"use client";
// An approved request that a person runs (a bench test): what to run, on which specimen, what to log, and the form
// that returns the result. The file goes to the ledger's upload space, then the result to the request; the server
// checks it (version, configuration, specimen, every measurement, every row) and records a refusal with its reasons.
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ApiError, newIdempotencyKey, v1 } from "@/lib/api";
import { paramsLine, prose, type Words } from "@/lib/loop";
import {
  decimalOf, expected, headerColumns, initialForm, missing, newUploadId, readHead, refusedReasons, resultBody,
  type ApprovedRequest, type Delimiter, type ResultForm, type ResultSpec,
} from "@/lib/result";
import { Spinner } from "./Icons";

const DELIMS: { v: Delimiter; label: string }[] = [{ v: ",", label: "comma" }, { v: ";", label: "semicolon" }, { v: "\t", label: "tab" }];

export function ReturnResult({ ledger, cid, requestId, spec, words, able, why, onDone }: {
  ledger: string; cid: string; requestId: string; spec: ResultSpec | undefined; words: Words; able: boolean;
  why: React.ReactNode; onDone(): void;
}) {
  const [req, setReq] = useState<ApprovedRequest | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let live = true;
    setReq(null);
    v1.request(ledger, cid, requestId).then((r) => { if (live) setReq(r as unknown as ApprovedRequest); })
      .catch((e) => { if (live) setLoadErr(e instanceof ApiError ? e.message : "Couldn’t read the request."); });
    return () => { live = false; };
  }, [ledger, cid, requestId]);
  if (loadErr) return <p className="lp-error" role="alert">{loadErr}</p>;
  if (!req || !spec) return <p className="ws-muted"><Spinner size={12} /> Reading the approved request…</p>;
  const e = expected(spec, req.action);
  const q = (n: string) => spec.quantities.find((x) => x.name === n);
  return (
    <div className="rr" aria-label="The approved test and its result">
      <dl className="rr-what">
        <div><dt>Run</dt><dd>{req.procedure ? prose(req.procedure) : paramsLine(req.params, words)}</dd></div>
        <div><dt>Approved configuration</dt><dd>{paramsLine(req.params, words)} · version {req.version}, content <code>{req.content_hash.slice(0, 12)}</code></dd></div>
        <div><dt>Specimen</dt><dd>{req.specimen?.id ? <>requested: <code>{req.specimen.id}</code></> : "any specimen in the fixture; name the one you test"}</dd></div>
        <div><dt>Log and return</dt><dd>{[...e.series.map((x) => `${x.label ?? x.name} (${x.unit}, a column of the logged file)`), ...e.values.map((x) => `${x.label ?? x.name}${x.kind === "category" ? "" : ` (${x.unit})`}`)].join("; ")}</dd></div>
      </dl>
      {(req.rejections ?? []).map((r) => (
        <div key={r.seq} className="lp-error" role="note"><p>A result returned for this request was refused (recorded at seq {r.seq}):</p><ul>{r.reasons.map((x) => <li key={x}>{prose(x)}</li>)}</ul></div>
      ))}
      {!able ? <p className="ws-muted">{why}</p>
        : open ? <ResultFormView req={req} spec={spec} ledger={ledger} cid={cid} quantity={q} onDone={onDone} onClose={() => setOpen(false)} />
          : <button type="button" className="btn" onClick={() => setOpen(true)}>Return the result…</button>}
    </div>
  );
}

function ResultFormView({ req, spec, ledger, cid, quantity, onDone, onClose }: {
  req: ApprovedRequest; spec: ResultSpec; ledger: string; cid: string; quantity(n: string): { label?: string; unit: string; categories?: string[]; kind: string } | undefined;
  onDone(): void; onClose(): void;
}) {
  const id = useId();
  const [f, setF] = useState<ResultForm>(() => initialForm(req, spec));
  const [file, setFile] = useState<File | null>(null);
  const [head, setHead] = useState<string[]>([]);
  const [busy, setBusy] = useState<"" | "upload" | "submit">("");
  const [note, setNote] = useState<{ text: string; reasons: string[]; error: boolean } | null>(null);
  const idem = useRef<string | null>(null);
  const e = useMemo(() => expected(spec, req.action), [spec, req.action]);
  const set = (patch: Partial<ResultForm>) => { idem.current = null; setF((x) => ({ ...x, ...patch })); };
  const cols = headerColumns(head, f.headerRow, f.delimiter);
  const gaps = missing(f, req, spec);

  const choose = async (fl: File | null) => {
    setFile(fl);
    setNote(null);
    if (!fl) { set({ file: "" }); setHead([]); return; }
    const text = await fl.slice(0, 64 * 1024).text();
    const h = readHead(text);
    setHead(h.lines);
    const names = headerColumns(h.lines, h.headerRow, h.delimiter);
    set({ file: fl.name, headerRow: h.headerRow, delimiter: h.delimiter, decimal: decimalOf(h.lines, h.headerRow, h.delimiter), skipRows: 0,
      timeColumn: names.find((n) => /time|^t\b|elapsed/i.test(n)) ?? names[0] ?? "",
      columns: Object.fromEntries(Object.entries(f.columns).map(([k, v]) => [k, { ...v, column: names.includes(v.column) ? v.column : "" }])) });
  };

  const send = async () => {
    setNote(null);
    const k = idem.current ??= newIdempotencyKey();
    let body = resultBody(f, req, spec);
    try {
      if (file && e.series.length) {
        setBusy("upload");
        const up = newUploadId();
        await v1.upload(ledger, up, file.name, file);
        body = { ...body, upload_id: up };
      }
      setBusy("submit");
      const out = await v1.submitResult(ledger, cid, req.request_id, body, k);
      setNote({ text: `Result recorded: request ${out.state === "completed" ? "completed with the uploaded result" : out.state.replace(/_/g, " ")}. The planner continues from it.`, reasons: [], error: false });
      onDone();
    } catch (err) {
      const x = err instanceof ApiError ? err : null;
      idem.current = null;
      if (x?.status === 422) {
        const reasons = refusedReasons(x.detail);
        setNote({ text: reasons.length ? "The server refused this result and recorded why. The request is still waiting for a result; correct it and send again." : x.message, reasons: reasons.length ? reasons : x.reasons, error: true });
        onDone();
      } else {
        setNote({ text: x ? (x.unreachable ? "The server didn’t answer. If nothing shows as recorded, send again: an identical result is recorded once." : x.status === 409 ? `The request changed: ${x.message}` : x.message) : "That didn’t go through.", reasons: [], error: true });
      }
    } finally {
      setBusy("");
    }
  };

  return (
    <form className="rr-form" onSubmit={(ev) => { ev.preventDefault(); if (!gaps.length && !busy) send(); }}>
      <fieldset>
        <legend>As run</legend>
        <div className="ws-field"><label className="ws-label" htmlFor={`${id}-sp`}>Specimen tested</label>
          <input id={`${id}-sp`} className="field" value={f.specimen} onChange={(x) => set({ specimen: x.target.value })} maxLength={64} spellCheck={false} /></div>
        {Object.entries(req.params).map(([k, p]) => (
          <div className="ws-field" key={k}><label className="ws-label" htmlFor={`${id}-p-${k}`}>{k} as executed{p.unit ? ` (${p.unit})` : ""}</label>
            <input id={`${id}-p-${k}`} className="field" inputMode="decimal" value={f.executed[k] ?? ""} onChange={(x) => set({ executed: { ...f.executed, [k]: x.target.value } })} /></div>
        ))}
      </fieldset>
      {e.series.length > 0 && (
        <fieldset>
          <legend>The logged file</legend>
          <div className="ws-field"><label className="ws-label" htmlFor={`${id}-file`}>File (CSV or TSV, as the logger wrote it)</label>
            <input id={`${id}-file`} type="file" accept=".csv,.tsv,.txt" onChange={(x) => choose(x.target.files?.[0] ?? null)} /></div>
          {head.length > 0 && (
            <>
              <pre className="rr-head" aria-label="The file's first lines">{head.slice(0, 14).map((l, i) => `${String(i + 1).padStart(3)}  ${l}`).join("\n")}</pre>
              <div className="rr-row">
                <div className="ws-field"><label className="ws-label" htmlFor={`${id}-hr`}>Lines before the header</label>
                  <input id={`${id}-hr`} className="field" type="number" min={0} max={200} value={f.headerRow} onChange={(x) => set({ headerRow: Math.max(0, Number(x.target.value) || 0) })} /></div>
                <div className="ws-field"><label className="ws-label" htmlFor={`${id}-sk`}>Non-data lines after the header (units)</label>
                  <input id={`${id}-sk`} className="field" type="number" min={0} max={10} value={f.skipRows} onChange={(x) => set({ skipRows: Math.max(0, Number(x.target.value) || 0) })} /></div>
                <div className="ws-field"><label className="ws-label" htmlFor={`${id}-dl`}>Delimiter</label>
                  <select id={`${id}-dl`} className="field" value={f.delimiter} onChange={(x) => set({ delimiter: x.target.value as Delimiter })}>{DELIMS.map((d) => <option key={d.label} value={d.v}>{d.label}</option>)}</select></div>
                <div className="ws-field"><label className="ws-label" htmlFor={`${id}-dc`}>Decimal mark</label>
                  <select id={`${id}-dc`} className="field" value={f.decimal} onChange={(x) => set({ decimal: x.target.value as "." | "," })}><option value=".">point</option><option value=",">comma</option></select></div>
              </div>
              <p className="ws-muted">Header: {cols.length ? cols.join(" · ") : "no columns on that line"}</p>
              <div className="rr-row">
                <div className="ws-field"><label className="ws-label" htmlFor={`${id}-tc`}>Time column</label>
                  <select id={`${id}-tc`} className="field" value={f.timeColumn} onChange={(x) => set({ timeColumn: x.target.value })}><option value="">choose</option>{cols.map((c) => <option key={c}>{c}</option>)}</select></div>
                <div className="ws-field"><label className="ws-label" htmlFor={`${id}-tu`}>Time unit</label>
                  <select id={`${id}-tu`} className="field" value={f.timeUnit} onChange={(x) => set({ timeUnit: x.target.value })}><option value="s">s</option><option value="min">min</option><option value="h">h</option></select></div>
                <div className="ws-field"><label className="ws-label" htmlFor={`${id}-to`}>Time zero is</label>
                  <input id={`${id}-to`} className="field" value={f.timeOrigin} onChange={(x) => set({ timeOrigin: x.target.value })} maxLength={200} /></div>
              </div>
              {e.series.map((s) => (
                <div className="rr-row" key={s.name}>
                  <div className="ws-field"><label className="ws-label" htmlFor={`${id}-c-${s.name}`}>{s.label ?? s.name}: column</label>
                    <select id={`${id}-c-${s.name}`} className="field" value={f.columns[s.name].column} onChange={(x) => set({ columns: { ...f.columns, [s.name]: { ...f.columns[s.name], column: x.target.value } } })}><option value="">choose</option>{cols.map((c) => <option key={c}>{c}</option>)}</select></div>
                  <div className="ws-field"><label className="ws-label" htmlFor={`${id}-u-${s.name}`}>its unit in the file</label>
                    <input id={`${id}-u-${s.name}`} className="field" value={f.columns[s.name].unit} onChange={(x) => set({ columns: { ...f.columns, [s.name]: { ...f.columns[s.name], unit: x.target.value } } })} maxLength={32} spellCheck={false} /></div>
                </div>
              ))}
            </>
          )}
        </fieldset>
      )}
      {e.values.length > 0 && (
        <fieldset>
          <legend>Values</legend>
          {e.values.map((s) => {
            const qq = quantity(s.name);
            const v = f.values[s.name];
            const put = (patch: Partial<typeof v>) => set({ values: { ...f.values, [s.name]: { ...v, ...patch } } });
            return (
              <div className="rr-row" key={s.name}>
                <div className="ws-field"><label className="ws-label" htmlFor={`${id}-v-${s.name}`}>{s.label ?? s.name}</label>
                  {qq?.kind === "category"
                    ? <select id={`${id}-v-${s.name}`} className="field" value={v.value} onChange={(x) => put({ value: x.target.value })}><option value="">choose</option>{(qq.categories ?? []).map((c) => <option key={c}>{c}</option>)}</select>
                    : <input id={`${id}-v-${s.name}`} className="field" inputMode="decimal" value={v.value} onChange={(x) => put({ value: x.target.value })} />}</div>
                {qq?.kind !== "category" && <div className="ws-field"><label className="ws-label" htmlFor={`${id}-vu-${s.name}`}>unit</label>
                  <input id={`${id}-vu-${s.name}`} className="field" value={v.unit} onChange={(x) => put({ unit: x.target.value })} maxLength={32} spellCheck={false} /></div>}
                <div className="ws-field"><label className="ws-label" htmlFor={`${id}-vs-${s.name}`}>from</label>
                  <input id={`${id}-vs-${s.name}`} className="field" value={v.source} onChange={(x) => put({ source: x.target.value })} maxLength={200} /></div>
              </div>
            );
          })}
        </fieldset>
      )}
      <div className="ws-field"><label className="ws-label" htmlFor={`${id}-n`}>Notes (what happened at the bench)</label>
        <textarea id={`${id}-n`} className="field" rows={2} value={f.notes} onChange={(x) => set({ notes: x.target.value })} maxLength={4000} /></div>
      {gaps.length > 0 && <p className="ws-muted">Before sending: {gaps.join("; ")}.</p>}
      <div className="ws-decide-row">
        <button className="btn ws-approve" type="submit" disabled={gaps.length > 0 || !!busy}>{busy ? <Spinner size={14} /> : null}{busy === "upload" ? "Uploading the file…" : busy === "submit" ? "Checking the result…" : "Send the result"}</button>
        <button className="btn btn-quiet" type="button" onClick={onClose} disabled={!!busy}>Close</button>
      </div>
      {note && (
        <div className={note.error ? "lp-error" : "lp-ok"} role={note.error ? "alert" : "status"}>
          <p>{note.text}</p>
          {note.reasons.length > 0 && <ul>{note.reasons.map((r) => <li key={r}>{prose(r)}</li>)}</ul>}
        </div>
      )}
    </form>
  );
}
