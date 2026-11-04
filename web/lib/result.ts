// A person's returned result for an approved request that a person runs (executor "human"): what the page shows
// before the upload, and the result body it sends (POST .../requests/{id}/results, the server's HumanUpload). The
// server checks everything again (version, units, configuration within tolerance, specimen, every measurement the
// action produces, every row of the table) and records a refusal with its reasons; nothing here decides validity.
import type { Param } from "./loop";

export interface ResultQuantity { name: string; unit: string; kind: string; label?: string; categories?: string[] }
export interface ResultAction {
  name: string; label?: string; executor: string; produces?: string[];
  parameters: { name: string; unit?: string; label?: string }[];
}
export interface ResultSpec { id: string; quantities: ResultQuantity[]; actions: ResultAction[] }

/** The approved request as GET .../requests/{id} returns it (the fields the form reads). */
export interface ApprovedRequest {
  request_id: string; version: number; state: string; action: string; executor: string; content_hash: string;
  params: Record<string, Param>; procedure?: string | null;
  specimen?: { id?: string | null; batch?: string | null; properties?: Record<string, unknown> } | null;
  rejections?: { seq: number; reasons: string[] }[];
}

/** What the action's result must hold: series (a column of the logged table each) and single values. */
export function expected(spec: ResultSpec, action: string): { series: ResultQuantity[]; values: ResultQuantity[] } {
  const a = spec.actions.find((x) => x.name === action);
  const q = (n: string) => spec.quantities.find((x) => x.name === n) ?? { name: n, unit: "", kind: "scalar" };
  const produced = (a?.produces ?? []).map(q);
  return { series: produced.filter((x) => x.kind === "series"), values: produced.filter((x) => x.kind !== "series") };
}

export type Delimiter = "," | ";" | "\t";

/** The file's first lines, its likely delimiter, and the likely header: the line before the first row whose fields
 *  are mostly numbers (a mode or step column may hold letters). A guess for the form to start from; the person sees
 *  the lines and can change it. */
export function readHead(text: string, maxLines = 60): { lines: string[]; delimiter: Delimiter; headerRow: number } {
  const lines = text.split(/\r?\n/).slice(0, maxLines);
  const counts: Record<Delimiter, number> = { ",": 0, ";": 0, "\t": 0 };
  for (const l of lines) for (const d of [",", ";", "\t"] as Delimiter[]) counts[d] += l.split(d).length - 1;
  const delimiter = (Object.keys(counts) as Delimiter[]).reduce((a, b) => (counts[b] > counts[a] ? b : a), ",");
  const numeric = (l: string) => {
    const f = l.split(delimiter).map((x) => x.trim());
    const n = f.filter((x) => /^[-+]?(\d+([.,]\d*)?|[.,]\d+)([eE][-+]?\d+)?$/.test(x)).length;
    return f.length > 1 && n * 2 > f.length;
  };
  const first = lines.findIndex(numeric);
  return { lines, delimiter, headerRow: first > 0 ? first - 1 : 0 };
}

/** The column names on the header line (header_row lines before it). */
export function headerColumns(lines: string[], headerRow: number, delimiter: Delimiter): string[] {
  const h = lines[headerRow];
  return h === undefined ? [] : h.split(delimiter).map((x) => x.trim()).filter(Boolean);
}

/** Whether the table's numbers use a decimal comma: a semicolon or tab file whose data has "3,1313"-like fields. */
export function decimalOf(lines: string[], headerRow: number, delimiter: Delimiter): "." | "," {
  if (delimiter === ",") return ".";
  const data = lines.slice(headerRow + 1).join("\n");
  return /(^|[;\t])-?\d+,\d+([;\t]|$)/m.test(data) ? "," : ".";
}

export interface ResultForm {
  specimen: string;
  executed: Record<string, string>;                 // parameter -> value as typed, in the approved unit
  file: string; headerRow: number; skipRows: number; delimiter: Delimiter; decimal: "." | ",";
  timeColumn: string; timeUnit: string; timeOrigin: string;
  columns: Record<string, { column: string; unit: string }>;           // series quantity -> its column
  values: Record<string, { value: string; unit: string; source: string }>;   // single values
  notes: string;
}

/** The form as it starts: the approved configuration, the requested specimen, each quantity in its system unit. */
export function initialForm(req: ApprovedRequest, spec: ResultSpec): ResultForm {
  const e = expected(spec, req.action);
  return {
    specimen: req.specimen?.id ?? "",
    executed: Object.fromEntries(Object.entries(req.params).map(([k, p]) => [k, String(p.value)])),
    file: "", headerRow: 0, skipRows: 0, delimiter: ",", decimal: ".", timeColumn: "", timeUnit: "s",
    timeOrigin: "start of the logged test",
    columns: Object.fromEntries(e.series.map((q) => [q.name, { column: "", unit: q.unit }])),
    values: Object.fromEntries(e.values.map((q) => [q.name, { value: "", unit: q.kind === "category" ? "" : q.unit, source: "the operator" }])),
    notes: "",
  };
}

/** What still keeps the form from being sent, in words (empty: send it). The server checks it all again. */
export function missing(f: ResultForm, req: ApprovedRequest, spec: ResultSpec): string[] {
  const e = expected(spec, req.action);
  const out: string[] = [];
  if (e.series.length && !f.file) out.push("choose the logged file");
  if (e.series.length && f.file && !f.timeColumn) out.push("name the time column");
  const used = new Map<string, string>();
  for (const q of e.series) {
    const c = f.columns[q.name]?.column;
    if (!c) out.push(`choose the column of ${q.label ?? q.name}`);
    else if (c === f.timeColumn) out.push(`${q.label ?? q.name}: ${c} is the time column`);
    else if (used.has(c)) out.push(`${c} is chosen for both ${used.get(c)} and ${q.label ?? q.name}`);
    else used.set(c, q.label ?? q.name);
  }
  for (const q of e.values) if (!f.values[q.name]?.value.trim()) out.push(`give ${q.label ?? q.name}`);
  for (const [k, p] of Object.entries(req.params)) {
    if (typeof p.value === "number" && !Number.isFinite(Number(f.executed[k]))) out.push(`give the executed ${k} as a number`);
  }
  return out;
}

/** The HumanUpload JSON: the approved version, the configuration as run, the table in the import grammar, values. */
export function resultBody(f: ResultForm, req: ApprovedRequest, spec: ResultSpec): Record<string, unknown> {
  const e = expected(spec, req.action);
  const executed: Record<string, number | string> = {};
  const units: Record<string, string> = {};
  for (const [k, p] of Object.entries(req.params)) {
    if (typeof p.value === "number") { executed[k] = Number(f.executed[k]); units[k] = p.unit; }
    else executed[k] = f.executed[k] ?? String(p.value);
  }
  const body: Record<string, unknown> = {
    request_id: req.request_id, content_hash: req.content_hash, executed_configuration: executed, executed_units: units,
    specimen_id: f.specimen.trim() || null, notes: f.notes.trim(),
    scalars: e.values.map((q) => {
      const v = f.values[q.name];
      const num = Number(v.value.replace(",", "."));
      return { quantity: q.name, value: q.kind === "category" ? v.value : num, unit: q.kind === "category" ? "" : v.unit, source: v.source.trim() || "the operator" };
    }),
  };
  if (e.series.length) {
    body.table = {
      file: f.file, header_row: f.headerRow, skip_rows: f.skipRows, time_column: f.timeColumn,
      ...(f.delimiter !== "," ? { delimiter: f.delimiter } : {}), ...(f.decimal !== "." ? { decimal: f.decimal } : {}),
      columns: Object.fromEntries(e.series.map((q) => [f.columns[q.name].column, { quantity: q.name, unit: f.columns[q.name].unit }])),
    };
    body.time = { unit: f.timeUnit, origin: f.timeOrigin.trim() || "start of the logged test", source: `the logged file's ${f.timeColumn} column` };
  }
  return body;
}

/** The reasons of a refused result (the server's 422 detail, recorded as result_rejected). */
export function refusedReasons(detail: unknown): string[] {
  const r = (detail as { result_rejected?: { reasons?: unknown } } | null)?.result_rejected?.reasons;
  return Array.isArray(r) ? r.map(String) : [];
}

/** A fresh upload id: 32 hex digits (the server's pattern). */
export function newUploadId(): string {
  const b = new Uint8Array(16);
  globalThis.crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}
