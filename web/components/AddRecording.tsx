"use client";
// Add your own recording: upload a video and its lab records, then register them as a new experiment
// (docs/BRING-YOUR-OWN.md). Scale, clock and voltage are declarations a person makes, each with where it comes from.
import { cloneElement, useId, useState } from "react";
import { peopleApi, PeopleError, type Manifest, type RecordedExperiment } from "@/lib/people";

const LIMIT = 500 * 2 ** 20;
const ID = /^[a-z0-9][a-z0-9_-]{2,63}$/;
const RESERVED = new Set(["manifest.json", "record.json", "record.sha256"]);

/** The server takes letters, digits, dot, dash and underscore, starting with a letter or digit. */
function safeName(name: string, taken: Set<string>): string {
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 ? name.slice(dot).replace(/[^A-Za-z0-9.]/g, "").slice(0, 12) : "";
  let stem = (dot > 0 ? name.slice(0, dot) : name).replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[^A-Za-z0-9]+/, "").slice(0, 100) || "file";
  let out = stem + ext;
  for (let i = 2; taken.has(out) || RESERVED.has(out); i++) out = `${stem}-${i}${ext}`;
  taken.add(out);
  return out;
}

const slug = (s: string) => s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
const num = (s: string) => (s.trim() === "" ? undefined : Number(s));
const ok = (n: number | undefined) => n !== undefined && Number.isFinite(n);
const mb = (n: number) => (n < 1e6 ? `${Math.max(1, Math.round(n / 1e3))} KB` : `${(n / 1e6).toFixed(n < 1e7 ? 1 : 0)} MB`);

function newUploadId() {
  const c = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID().replaceAll("-", "");
  const b = new Uint8Array(16);
  c.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

function FilePick({ label, hint, accept, file, onFile }: { label: string; hint: string; accept: string; file: File | null; onFile: (f: File | null) => void }) {
  return (
    <label className={`file-pick ${file ? "has-file" : ""}`}>
      <input type="file" accept={accept} onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
      <span className="file-pick-label">{file ? file.name : label}</span>
      <span className="file-pick-hint">{file ? mb(file.size) : hint}</span>
    </label>
  );
}

/** A labelled field; its hint describes the input rather than joining its name. */
function Field({ label, hint, wide, children }: { label: string; hint?: string; wide?: boolean; children: React.ReactElement<{ id?: string; "aria-describedby"?: string }> }) {
  const id = useId();
  return (
    <div className={`join-field ${wide ? "is-wide" : ""}`}>
      <label htmlFor={id}>{label}</label>
      {cloneElement(children, { id, "aria-describedby": hint ? `${id}-hint` : undefined })}
      {hint && <span id={`${id}-hint`} className="add-hint">{hint}</span>}
    </div>
  );
}

function Choice<T extends string>({ name, value, options, onChange }: { name: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="add-choice" role="radiogroup" aria-label={name}>
      {options.map(([v, text]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} className={value === v ? "is-on" : ""} onClick={() => onChange(v)}>{text}</button>
      ))}
    </div>
  );
}

function Section({ title, open, onToggle, children }: { title: string; open: boolean; onToggle: () => void; children: React.ReactNode }) {
  return (
    <section className={`add-section ${open ? "is-open" : ""}`}>
      <div className="add-section-head">
        <h3>{title}</h3>
        <button type="button" className="text-quiet" aria-expanded={open} onClick={onToggle}>{open ? "Leave out" : "Add"}</button>
      </div>
      {open && <div className="add-section-body">{children}</div>}
    </section>
  );
}

type Phase = { kind: "idle" } | { kind: "upload"; sent: number; total: number } | { kind: "register" };

export function AddRecording({ accessKey, setAccessKey, author: defaultAuthor, onAdded }: {
  accessKey: string; setAccessKey: (v: string) => void; author: string; onAdded: (x: RecordedExperiment) => void;
}) {
  const [title, setTitle] = useState("");
  const [id, setId] = useState("");
  const [idEdited, setIdEdited] = useState(false);
  const [citation, setCitation] = useState("");
  const [licence, setLicence] = useState("");
  const [author, setAuthor] = useState(defaultAuthor);
  const [video, setVideo] = useState<File | null>(null);

  const [scaleOn, setScaleOn] = useState(false);
  const [scaleKind, setScaleKind] = useState<"px" | "bar">("bar");
  const [umPerPx, setUmPerPx] = useState("");
  const [barPx, setBarPx] = useState("");
  const [barUm, setBarUm] = useState("");
  const [scaleUnc, setScaleUnc] = useState("");
  const [scaleSource, setScaleSource] = useState("");

  const [clockOn, setClockOn] = useState(false);
  const [clockKind, setClockKind] = useState<"csv" | "interval">("csv");
  const [stamps, setStamps] = useState<File | null>(null);
  const [interval, setIntervalS] = useState("");
  const [intervalUnc, setIntervalUnc] = useState("");
  const [clockSource, setClockSource] = useState("");

  const [voltOn, setVoltOn] = useState(false);
  const [volts, setVolts] = useState<File | null>(null);
  const [offset, setOffset] = useState("0");
  const [voltSource, setVoltSource] = useState("");

  const [notesOn, setNotesOn] = useState(false);
  const [notes, setNotes] = useState("");

  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [error, setError] = useState("");

  const expId = idEdited ? id : slug(title);
  const files = [video, clockOn && clockKind === "csv" ? stamps : null, voltOn ? volts : null].filter((f): f is File => !!f);

  // What still stops it, in the order the form reads; the button says the first one.
  const missing = (() => {
    if (!title.trim()) return "Add a title";
    if (!ID.test(expId)) return "Choose an ID: 3–64 lowercase letters, digits, - or _";
    if (!citation.trim()) return "Add a citation";
    if (!licence.trim()) return "Add the licence or usage terms";
    if (!author.trim()) return "Add who declares these records";
    if (!video) return "Choose the recording";
    if (scaleOn) {
      if (scaleKind === "px" ? !ok(num(umPerPx)) : !ok(num(barPx)) || !ok(num(barUm))) return "Give the scale as numbers";
      if (scaleUnc.trim() && !ok(num(scaleUnc))) return "Give the scale uncertainty as a number";
      if (!scaleSource.trim()) return "Say where the scale comes from";
    }
    if (clockOn) {
      if (clockKind === "csv" ? !stamps : !ok(num(interval))) return clockKind === "csv" ? "Choose the timestamps file" : "Give the interval in seconds";
      if (clockKind === "interval" && intervalUnc.trim() && !ok(num(intervalUnc))) return "Give the interval uncertainty as a number";
      if (!clockSource.trim()) return "Say where the timing comes from";
    }
    if (voltOn) {
      if (!volts) return "Choose the voltage file";
      if (!ok(num(offset))) return "Give the log time at the first frame";
      if (!voltSource.trim()) return "Say where the alignment comes from";
    }
    if (!accessKey.trim()) return "Add your access key";
    const big = files.find((f) => f.size > LIMIT);
    if (big) return `${big.name} is over 500 MB`;
    return "";
  })();
  const busy = phase.kind !== "idle";

  const submit = async () => {
    if (missing || busy || !video) return;
    setError("");
    const taken = new Set<string>();
    const named: [string, File][] = [];
    const name = (f: File) => { const n = safeName(f.name, taken); named.push([n, f]); return n; };
    const manifest: Manifest = {
      id: expId, title: title.trim(), citation: citation.trim(), licence: licence.trim(), author: author.trim(), video: name(video),
    };
    if (scaleOn) {
      manifest.scale = {
        ...(scaleKind === "px" ? { um_per_px: num(umPerPx)! } : { bar_px: num(barPx)!, bar_um: num(barUm)! }),
        ...(scaleUnc.trim() ? { uncertainty: num(scaleUnc)! } : {}),
        source: scaleSource.trim(),
      };
    }
    if (clockOn) {
      manifest.clock = clockKind === "csv" ? { timestamps_csv: name(stamps!), source: clockSource.trim() }
        : { interval_s: num(interval)!, ...(intervalUnc.trim() ? { uncertainty_s: num(intervalUnc)! } : {}), source: clockSource.trim() };
    }
    if (voltOn) manifest.voltage = { csv: name(volts!), offset_s: num(offset)!, source: voltSource.trim() };
    if (notesOn && notes.trim()) manifest.notes = notes.trim();

    const uploadId = newUploadId();
    const total = named.reduce((s, [, f]) => s + f.size, 0);
    let done = 0;
    try {
      setPhase({ kind: "upload", sent: 0, total });
      for (const [n, f] of named) {
        await peopleApi().upload(uploadId, n, f, accessKey.trim(), (sent) => setPhase({ kind: "upload", sent: done + Math.min(sent, f.size), total }));
        done += f.size;
      }
      setPhase({ kind: "register" });
      const added = await peopleApi().addExperiment(uploadId, manifest, accessKey.trim());
      setPhase({ kind: "idle" });
      onAdded(added);
    } catch (err) {
      setError(err instanceof PeopleError ? err.message : "Couldn’t add it. Try again.");
      setPhase({ kind: "idle" });
    }
  };

  const share = phase.kind === "upload" && phase.total ? phase.sent / phase.total : 0;

  return (
    <div className="add" aria-busy={busy}>
      <p className="add-lede">Add a recording from your lab. Its records stay with it, so every answer about it can be checked against them.</p>

      <div className="add-grid">
        <Field label="Title" wide>
          <input className="field" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Cell 042, operando growth" />
        </Field>
        <Field label="Citation" hint="A paper, or a lab notebook reference">
          <input className="field" value={citation} onChange={(e) => setCitation(e.target.value)} placeholder="Lab notebook 2026-10-28, p. 42" />
        </Field>
        <Field label="Licence or usage terms">
          <input className="field" value={licence} onChange={(e) => setLicence(e.target.value)} placeholder="Internal lab use" />
        </Field>
        <Field label="Declared by" hint="Who stands behind these records">
          <input className="field" value={author} onChange={(e) => setAuthor(e.target.value)} />
        </Field>
        <Field label="ID" hint="Can’t be changed later">
          <input className="field" value={expId} spellCheck={false}
            onChange={(e) => { setIdEdited(true); setId(e.target.value.toLowerCase()); }} placeholder="cell-042" />
        </Field>
      </div>

      <FilePick label="Choose the recording" hint="A video file, up to 500 MB" accept="video/*,.mp4,.mov,.avi,.mkv,.tif,.tiff" file={video} onFile={setVideo} />

      <p className="add-optional">Optional records. Findings that need one are refused without it.</p>

      <Section title="Scale" open={scaleOn} onToggle={() => setScaleOn(!scaleOn)}>
        <Choice name="How the scale is given" value={scaleKind} onChange={setScaleKind} options={[["bar", "A scale bar"], ["px", "µm per pixel"]]} />
        <div className="add-row">
          {scaleKind === "bar" ? (
            <>
              <Field label="Bar length, pixels"><input className="field" inputMode="decimal" value={barPx} onChange={(e) => setBarPx(e.target.value)} /></Field>
              <Field label="Bar length, µm"><input className="field" inputMode="decimal" value={barUm} onChange={(e) => setBarUm(e.target.value)} /></Field>
            </>
          ) : (
            <Field label="µm per pixel"><input className="field" inputMode="decimal" value={umPerPx} onChange={(e) => setUmPerPx(e.target.value)} /></Field>
          )}
          <Field label="Uncertainty, µm per pixel (optional)"><input className="field" inputMode="decimal" value={scaleUnc} onChange={(e) => setScaleUnc(e.target.value)} /></Field>
        </div>
        <Field label="Where does this come from?" wide>
          <input className="field" value={scaleSource} onChange={(e) => setScaleSource(e.target.value)} placeholder="Calibration slide, notebook p. 42" />
        </Field>
      </Section>

      <Section title="Clock" open={clockOn} onToggle={() => setClockOn(!clockOn)}>
        <p className="add-note">The video’s frame rate isn’t used as a clock: export software can speed playback up or repeat images, so timing has to come from the acquisition.</p>
        <Choice name="How timing is given" value={clockKind} onChange={setClockKind} options={[["csv", "A timestamp per frame"], ["interval", "A fixed interval"]]} />
        {clockKind === "csv" ? (
          <FilePick label="Choose the timestamps" hint="CSV with frame,time_s, one row per frame" accept=".csv,text/csv" file={stamps} onFile={setStamps} />
        ) : (
          <div className="add-row">
            <Field label="Seconds between images"><input className="field" inputMode="decimal" value={interval} onChange={(e) => setIntervalS(e.target.value)} /></Field>
            <Field label="Uncertainty, seconds (optional)"><input className="field" inputMode="decimal" value={intervalUnc} onChange={(e) => setIntervalUnc(e.target.value)} /></Field>
          </div>
        )}
        <Field label="Where does this come from?" wide>
          <input className="field" value={clockSource} onChange={(e) => setClockSource(e.target.value)} placeholder="Acquisition computer timestamp export" />
        </Field>
      </Section>

      <Section title="Voltage" open={voltOn} onToggle={() => setVoltOn(!voltOn)}>
        <FilePick label="Choose the voltage log" hint="CSV with time_s,voltage_V" accept=".csv,text/csv" file={volts} onFile={setVolts} />
        <div className="add-row">
          <Field label="Log time at the video’s first frame, s"><input className="field" inputMode="decimal" value={offset} onChange={(e) => setOffset(e.target.value)} /></Field>
        </div>
        <Field label="Where does this come from?" wide>
          <input className="field" value={voltSource} onChange={(e) => setVoltSource(e.target.value)} placeholder="Potentiostat export; video start marked at 12 s" />
        </Field>
      </Section>

      <Section title="Notes" open={notesOn} onToggle={() => setNotesOn(!notesOn)}>
        <textarea className="field add-notes" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} aria-label="Notes" />
      </Section>

      <div className="start-run">
        <label className="join-field is-wide">
          <span>Access key</span>
          <input className="field" type="password" value={accessKey} onChange={(e) => setAccessKey(e.target.value)} autoComplete="off" />
        </label>
      </div>

      {error && <p className="start-error" role="alert">{error}</p>}

      {busy ? (
        <div className="add-progress" role="status" aria-live="polite">
          <div className="add-bar"><span style={{ transform: `scaleX(${phase.kind === "register" ? 1 : share})` }} /></div>
          <span>{phase.kind === "upload" ? `Uploading ${mb(phase.sent)} of ${mb(phase.total)}` : "Checking the records…"}</span>
        </div>
      ) : (
        <div className="add-submit">
          <button className="btn btn-start" type="button" disabled={!!missing} onClick={submit}>Add recording</button>
          {missing && <span className="add-hint">{missing}</span>}
        </div>
      )}
    </div>
  );
}
