// Plain-language vocabulary for the screen. The contract's machine names never reach the viewer.
// The runtime writes titles and gate reasons for logs; everything here turns them into short human lines.
import type { Check, Claim, Event, Measurement, Review } from "./contracts";
import type { FindingState } from "./store";

export const STATE_WORD: Record<FindingState, string> = {
  checking: "Checking",
  earned: "Supported",
  refused: "Turned down",
  "not-proven": "Not proven yet",
};

// ---------------------------------------------------------------- text hygiene

/** Remove ledger ids, bracketed citations of ids and code-ish units from free text written by the runtime. */
export function clean(s: string): string {
  return s
    .replace(/\s*\[(?:claim|m|rev|run|lesson)_[0-9a-f]+(?:,\s*(?:claim|m|rev|run|lesson)_[0-9a-f]+)*\]/g, "")
    .replace(/\s*\((?:[a-z ]+\s)?(?:m|claim|rev|run|lesson)_[0-9a-f]{6,}(?:,\s*(?:[a-z ]+\s)?(?:m|claim|rev|run|lesson)_[0-9a-f]{6,})*\)/g, "")
    .replace(/\s*(?:Measurement|Outlines|Lesson saved)? ?(?:id )?\(?(?:sha256:[0-9a-f]{8,}|(?:m|claim|rev|run|lesson)_[0-9a-f]{6,})\)?\.?/g, "")
    .replace(/\bum\/s\b/g, "µm/s")
    .replace(/(\d)\s?um\b/g, "$1 µm")
    .replace(/\bum\b/g, "µm")
    .replace(/~\s?(\d)/g, "about $1")
    .replace(/(^|[\s(])-(?=\d)/g, "$1−")
    .replace(/±(?=\d)/g, "± ")
    .replace(/\bsegmentation\b/g, "outlining")
    .replace(/\s+\./g, ".")
    .replace(/\s{2,}/g, " ")
    .trim();
}

const cap = (s: string) => s.replace(/^\s*\w/, (c) => c.toUpperCase());

/** "object 2" in runtime labels is one of the tracked filaments; say so when the track names agree. */
export function subjectWord(s: string, subjects: string[] = []): string {
  return s.replace(/\bobject (\d+)\b/g, (m, n) => subjects.find((x) => x.endsWith(` ${n}`)) ?? m);
}

// ---------------------------------------------------------------- the story

/** What each runtime tool is doing, in the viewer's words. null: the tool is told by other lines. */
const TOOL_LINE: Record<string, string | null> = {
  inspect_video: "Looking at the video",
  lookup_calibration: "Checking the experiment’s records",
  read_cell_voltage: "Reading the battery’s voltage",
  segment_images: "Outlining each image with Meta SAM 3.1",
  track_objects: "Tracking with Meta SAM 3.1",
  measure_growth: "Measuring the filaments",
  run_analysis_code: "Running an analysis",
  propose_finding: null,
  record_lesson: null,
};

/** Tools the runtime names: their finished title repeats the started one, so both share one line. */
export const isRuntimeTool = (actor: string) => actor in TOOL_LINE;

export function toolLine(e: Event): string | null {
  if (e.actor in TOOL_LINE) return TOOL_LINE[e.actor];
  return e.title.replace(/\s*[—–-]\s*failed$/i, "");
}

/**
 * An orchestrator's plan line. The runtime cuts titles at 90 characters, so the full sentence comes from `detail`.
 * When a line looks back and then ahead ("I've done X — now I'll do Y"), only the part ahead is kept.
 */
export function planLine(e: Event): string {
  const full = e.detail && e.detail.startsWith(e.title.slice(0, 30)) ? e.detail : e.title;
  const parts = full.split(/\s+[—–]\s+|\s+-\s+/);
  const ahead = parts.length > 1 ? parts[parts.length - 1] : full;
  return cap(clean(ahead));
}

/** Seconds as "90 s", "3.5 s". */
export function seconds(v: number) {
  return `${Math.abs(v) >= 20 ? Math.round(v) : +v.toPrecision(2)} s`;
}

/** Is this measurement a moment on the recording's clock (a short, a recovery)? */
export function isMoment(m: Measurement | undefined): m is Measurement & { value: number } {
  return !!m && typeof m.value === "number" && m.unit === "s" && /_time$/.test(m.quantity) && !/elapsed/.test(m.quantity);
}

/** "cycle 1 charge (2 mA/cm2): below 0 V" -> "First charge: below 0 V". Other labels pass through. */
export function plainLabel(label: string): string {
  const m = label.match(/^cycle (\d+) (charge|discharge)\s*(?:\([^)]*\))?:\s*(.*)$/i);
  if (!m) return label;
  const nth = ["First", "Second", "Third", "Fourth"][+m[1] - 1] ?? `Cycle ${m[1]}`;
  return `${nth} ${m[2].toLowerCase()}: ${m[3]}`;
}

/**
 * Seconds on the recording's clock for a measurement that marks a moment, or null. Times in seconds are on the
 * recording's clock already. A time in hours from the start of a charge is placed on the log only when that lands
 * inside the measurement's own recorded time range (true when the charge starts with the log, as cycle 1 does); a
 * later cycle's time never lands there, so it is never drawn.
 */
export function momentSeconds(m: Measurement | undefined): number | null {
  if (isMoment(m)) return m.value;
  if (!m || typeof m.value !== "number" || m.unit !== "h" || m.quantity !== "elapsed_time" || !m.time_range) return null;
  const t = m.value * 3600;
  return t >= m.time_range[0] && t <= m.time_range[1] ? t : null;
}

/** Keep a number with its unit on one line ("0 V", "10 h"). */
export const keepUnits = (s: string) => s.replace(/(\d) (V|mV|h|s|min|µm|um|mm)\b/g, "$1 $2");

/** Recordings longer than two hours are told in hours. */
export const isLong = (spanSeconds: number) => spanSeconds > 7200;

/** A time on a recording's clock: m:ss for short recordings, hours for long ones ("7.4 h"). */
export function timeOn(s: number, long: boolean): string {
  if (!long) return clock(s);
  const h = s / 3600;
  return `${Math.abs(h - Math.round(h)) < 0.05 ? Math.round(h) : h.toFixed(1)} h`;
}

/** "Battery shorts" -> "Battery shorts at 90, 375 and 389 s". */
export function momentsLine(label: string, values: number[]) {
  const vs = values.map((v) => String(Math.round(v)));
  const list = vs.length > 1 ? `${vs.slice(0, -1).join(", ")} and ${vs[vs.length - 1]}` : vs[0];
  return `${label} at ${list} s`;
}

/** One line for a scalar measurement, or null for bulk ones (per-frame tables) that other lines describe. */
export function measurementLine(m: Measurement, subjects: string[]): string | null {
  if (Array.isArray(m.value)) return null;
  const label = plainLabel(subjectWord(m.label ?? cap(m.quantity.replace(/_/g, " ")), subjects));
  return `${label}: ${valueWord(m)}`;
}

/** A failed tool, in plain words. */
export const FAILED_WORD = "didn’t work";

// ---------------------------------------------------------------- checks and reasons

const CHECK_LABEL: Record<string, string> = {
  // design fixtures
  ancestry: "Every number traces to frames",
  units: "Units are consistent",
  distinct_frames: "Frames are distinct images",
  time_base: "Time between images is known",
  time_base_recorded: "Time between images is known",
  tracker_agreement: "Two trackers agree",
  in_frame: "Visible in the cited frames",
  enough_points: "Enough images to test a trend",
  voltage_corroboration: "Cell voltage agrees",
  // runtime gate
  "evidence is sound": "Every number comes from a step that worked",
  "scale and timing recorded": "Scale and timing are on record",
  "units match": "Units match",
  "value in range": "The number is in range",
  "range is specific": "The range is narrow enough to mean something",
  "trend is steady": "It holds steady",
  "trend is increasing": "It rises",
  "trend is decreasing": "It falls",
  "finding is well-formed": "Stated as something checkable",
  "traced to a recording": "Traced to the recording",
};

export function checkLabel(name: string) {
  return CHECK_LABEL[name] ?? cap(name.replace(/[_.]+/g, " "));
}

/** The gate may run one check per piece of evidence; the viewer sees each kind of check once. */
export function groupChecks(checks: Check[]): Check[] {
  const out: Check[] = [];
  for (const c of checks) {
    const prev = out.find((x) => x.name === c.name);
    if (!prev) out.push({ ...c });
    else {
      prev.passed = prev.passed && c.passed;
      if (!c.passed) prev.detail = c.detail;
    }
  }
  return out;
}

const UNIT_NOUN: Record<string, string> = {
  s: "a time", min: "a time", h: "a time",
  um: "a length", "µm": "a length", mm: "a length", px: "a length",
  "um/s": "a speed", "µm/s": "a speed", V: "a voltage", "px/mm": "a scale",
};
const unitNoun = (u: string) => UNIT_NOUN[u] ?? `“${unitWord(u)}”`;

const TOOL_SOURCE: Record<string, string> = {
  read_cell_voltage: "the voltage log",
  measure_growth: "the filament outlines",
  lookup_calibration: "the experiment’s records",
  segment_images: "the Meta SAM 3.1 outlines",
  track_objects: "Meta SAM 3.1 tracking",
  inspect_video: "the video",
  run_analysis_code: "an analysis",
};

const num = (s: string) => {
  const n = +s;
  return Number.isFinite(n) ? (Math.abs(n) >= 100 ? String(Math.round(n * 10) / 10) : String(+n.toPrecision(3))) : s;
};
const minus = (s: string) => s.replace(/(^|[\s(])-(?=\d)/g, "$1−");

/** A gate check's detail line, without tool names, brackets or code units. */
export function checkDetail(detail: string, subjects: string[] = []): string {
  let d = plainLabel(subjectWord(detail, subjects));
  if (d === "ok") return "";
  let m = d.match(/^(.*): from a successful (\w+) run$/);
  if (m) return `${m[1]}, from ${TOOL_SOURCE[m[2]] ?? "a step that worked"}`;
  m = d.match(/^(.*) in (\S+): calibration on record$/);
  if (m) return `${m[1]}: ${/^(s|min|h)$/.test(m[2]) ? "recorded clock" : "scale bar"} on record`;
  m = d.match(/^all in (\S+)$/);
  if (m) return `All in ${m[1] === "s" ? "seconds" : m[1] === "h" ? "hours" : unitWord(m[1])}`;
  m = d.match(/^expected (\S+), got (\S+)$/);
  if (m) return `Needs ${unitNoun(m[1])}, got ${unitNoun(m[2])}`;
  m = d.match(/^(-?[\d.]+) ± ([\d.]+) (\S+) vs \[(-?[\d.]+), (-?[\d.]+)\](: range too wide)?/);
  if (m) return minus(`${num(m[1])} ± ${num(m[2])} ${unitWord(m[3])}, against ${num(m[4])} to ${num(m[5])} ${unitWord(m[3])}${m[6] ? ": too wide" : ""}`);
  m = d.match(/^slope (-?[\d.]+) (\S+) per s \(95% range (-?[\d.]+) to (-?[\d.]+)\), (\d+) points$/);
  if (m) return minus(`Changes ${num(m[1])} ${unitWord(m[2])} a second (likely between ${num(m[3])} and ${num(m[4])}), over ${m[5]} images`);
  d = clean(d);
  return cap(d);
}

/**
 * One plain sentence for why the gate decided. Hand-written reasons (design fixtures) are kept; the runtime's terse
 * reasons are rebuilt from the checks.
 */
export function plainReason(review: Review | undefined, claim?: Claim): string | null {
  if (!review) return null;
  const checks = review.checks ?? [];
  const failed = checks.find((c) => !c.passed);
  const terse = /^Not enough evidence|^The evidence supports it\.?$|^The measurements (contradict it|can't settle it)|expected \S+, got \S+/
    .test(review.reason);
  if (!terse) return clean(review.reason);

  if (!failed) {
    const inRange = checks.find((c) => c.name === "value in range");
    const m = inRange?.detail.match(/^(-?[\d.]+) ± ([\d.]+) (\S+) vs \[(-?[\d.]+), (-?[\d.]+)\]$/);
    if (m) return minus(`Measured ${num(m[1])} ${unitWord(m[3])}, give or take ${num(m[2])}: inside the ${num(m[4])}–${num(m[5])} ${unitWord(m[3])} it claims.`);
    const trend = checks.find((c) => c.name.startsWith("trend"));
    const t = trend?.detail.match(/(\d+) points$/);
    if (trend && t) return `The trend holds across ${t[1]} images.`;
    return "Every check passed.";
  }
  const units = failed.detail.match(/^expected (\S+), got (\S+)$/);
  if (units) {
    if (claim?.predicate.type === "events_coincide") {
      return `Can’t line these up: one was measured as ${unitNoun(units[2])}, not ${unitNoun(units[1])}.`;
    }
    return `It compares ${unitNoun(units[1])} with ${unitNoun(units[2])}.`;
  }
  const range = failed.name === "value in range" && failed.detail.match(/^(-?[\d.]+) ± ([\d.]+) (\S+) vs \[(-?[\d.]+), (-?[\d.]+)\]$/);
  if (range) {
    const [, v, u, unit, lo, hi] = range;
    const said = `Measured ${num(v)} ${unitWord(unit)}, give or take ${num(u)}`;
    if (review.verdict === "refuted") return minus(`${said}: outside the ${num(lo)}–${num(hi)} ${unitWord(unit)} it states.`);
    if (review.verdict === "inconclusive") return minus(`${said}: too uncertain to place inside ${num(lo)}–${num(hi)} ${unitWord(unit)}.`);
  }
  const wide = failed.name === "range is specific" && failed.detail.match(/^(-?[\d.]+) ± ([\d.]+) (\S+) vs \[(-?[\d.]+), (-?[\d.]+)\]/);
  if (wide) {
    const [, v, u, unit, lo, hi] = wide;
    return minus(`Measured ${num(v)} ${unitWord(unit)}, give or take ${num(u)}: a ${num(lo)}–${num(hi)} ${unitWord(unit)} range is too wide to say anything.`);
  }
  if (failed.name === "finding is well-formed") {
    if (/^exists findings/.test(failed.detail)) return "It only says something happened, without a number the checks can test.";
    const few = failed.detail.match(/^trend takes (\d+) measurement\(s\), got (\d+)$/);
    if (few) return `A trend needs ${few[1]} measurements; it gave ${few[2]}.`;
    const one = failed.detail.match(/^value_within takes (\d+) measurement\(s\), got (\d+)$/);
    if (one) return `It cited ${one[2]} measurements where the check needs ${one[1]}.`;
    if (/^value_within compares single numbers/.test(failed.detail)) return "It cited a series of readings where the check needs one number.";
  }
  switch (failed.name) {
    case "value in range": return "The measured number falls outside what it claims.";
    case "traced to a recording": return "It can’t be traced to one recording.";
    case "evidence is sound": return "Some of its evidence came from a step that failed.";
    case "scale and timing recorded": return "The scale or timing it needs isn’t on record.";
  }
  if (failed.name.startsWith("trend")) return "The trend isn’t clear enough in the measurements.";
  return clean(`${checkLabel(failed.name)}: ${failed.detail}`);
}

/** The critic's verdict, for the small "second opinion" credit. */
export const SECOND_WORD: Record<string, string> = {
  supported: "agrees",
  refuted: "disagrees",
  unsupported: "disagrees",
  inconclusive: "unsure",
};

/** "accounts/fireworks/models/muse-spark-open" -> "Muse". */
export function modelName(model: string | null | undefined) {
  if (!model) return "a second model";
  if (/muse/i.test(model)) return "Muse";
  const last = model.split("/").pop() ?? model;
  return cap(last.replace(/[-_]+/g, " "));
}

/**
 * "Muse Spark Open on Fireworks", from the model paths recorded in a run's reviews
 * ("accounts/fireworks/models/muse-spark-open"). null when the bundle names no model.
 */
export function runModel(models: (string | null | undefined)[]): string | null {
  const m = models.find((x): x is string => !!x);
  if (!m) return null;
  const name = (m.split("/").pop() ?? m).split(/[-_]+/).map((w) => cap(w)).join(" ");
  const host = m.match(/^accounts\/([^/]+)\//)?.[1];
  return host ? `${name} on ${cap(host)}` : name;
}

/** A model id as people say it: "muse-wonder-1.0" is "Muse Wonder", the Fireworks Spark Open is "Muse Spark Open". */
export function roleModel(id: string | null | undefined): string | null {
  if (!id) return null;
  const base = (id.split("/").pop() ?? id).replace(/-\d+(\.\d+)*$/, "");
  return base.split(/[-_]+/).map((w) => cap(w)).join(" ");
}

/** "Oct 28", from a recorded unix time. */
export const dayWord = (unix: number) =>
  new Date(unix * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

/** What an earned finding rests on, when the second opinion disagreed with it. */
export function disagreedLine(ms: (Measurement | undefined)[]): string {
  const voltage = ms.some((m) => m && /voltage|collapse|recover/.test(m.quantity));
  const time = ms.some((m) => m && /_time$/.test(m.quantity));
  const rests = voltage ? `${time ? "this time" : "it"} comes from the cell’s voltage log` : "it rests on the measurements";
  return `Second opinion disagreed. It’s advice only; ${rests}.`;
}

// ---------------------------------------------------------------- numbers

const UNIT: Record<string, string> = {
  "um/s": "µm/s", um: "µm", "px/mm": "px per mm", s: "s", px: "px", V: "V", frames: "frames", images: "images",
};
export const unitWord = (u: string) => UNIT[u] ?? u;

const fmt = (n: number) => {
  const s = Math.abs(n) >= 100 ? n.toFixed(1).replace(/\.0$/, "") : String(+n.toPrecision(3));
  return s.replace(/^-/, "−");
};

/** "184.9 px per mm", "2–7 s", "13 values, 230–242 µm", or "—" when the number does not exist yet. */
export function valueWord(m: Measurement | undefined): string {
  if (!m) return "—";
  if (Array.isArray(m.value)) {
    if (m.value.length === 2) return `${fmt(m.value[0])}–${fmt(m.value[1])} ${unitWord(m.unit)}`;
    const lo = Math.min(...m.value), hi = Math.max(...m.value);
    return `${m.value.length} values, ${fmt(lo)}–${fmt(hi)} ${unitWord(m.unit)}`;
  }
  return `${fmt(m.value)} ${unitWord(m.unit)}`;
}

/** Uncertainty in plain words. */
export function uncertaintyWord(m: Measurement | undefined): string | null {
  if (!m) return "Not measured yet";
  const u = m.uncertainty;
  if (u.kind === "none") return u.method && u.method !== "count" ? cap(clean(u.method)) : null;
  if (u.value == null) return cap(clean(u.method));
  const pm = `${fmt(u.value)} ${unitWord(m.unit)}`;
  switch (u.kind) {
    case "std": return `Give or take ${pm} (one standard deviation)`;
    case "ci95": return `Give or take ${pm} (95% range)`;
    case "bound": return `Give or take ${pm}`;
    case "resolution": return `To the nearest ${pm}`;
  }
}

export function money(v: number | null) {
  if (v == null) return "—";
  if (v > 0 && v < 0.01) return "<$0.01";
  return `$${v.toFixed(2)}`;
}

/** Seconds as m:ss. */
export function clock(s: number) {
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
}

export function framesWord(frames: number[]) {
  if (!frames.length) return "";
  if (frames.length === 1) return `Frame ${frames[0]}`;
  const contiguous = frames.every((f, i) => i === 0 || f === frames[i - 1] + 1);
  if (frames.length === 2 || contiguous) return `Frames ${frames[0]}–${frames[frames.length - 1]}`;
  return `Frames ${frames.slice(0, -1).join(", ")} and ${frames[frames.length - 1]}`;
}

/** The runtime's final answer, split into a headline sentence and the rest. */
export function answerParts(answer: string | null): { lead: string; rest: string } | null {
  if (!answer) return null;
  const a = clean(answer);
  const i = a.search(/\.\s+[A-Z]/);
  return i < 0 ? { lead: a, rest: "" } : { lead: a.slice(0, i + 1), rest: a.slice(i + 1).trim() };
}

// ---------------------------------------------------------------- tool steps in the conversation

/** A tool step as one plain line: present tense while it runs, past tense once done. */
const TOOL_VERBS: Record<string, [string, string]> = {
  inspect_video: ["Looking at the recording", "Looked at the recording"],
  lookup_calibration: ["Checking the lab’s records", "Checked the lab’s records"],
  read_cell_voltage: ["Reading the cell’s voltage log", "Read the cell’s voltage log"],
  segment_images: ["Outlining the images with SAM 3.1", "Outlined the images with SAM 3.1"],
  track_objects: ["Following the filaments with SAM 3.1", "Followed the filaments with SAM 3.1"],
  measure_growth: ["Measuring the filaments", "Measured the filaments"],
  run_analysis_code: ["Running an analysis", "Ran an analysis"],
};

export const isPhrasedTool = (tool: string | undefined): tool is string => !!tool && tool in TOOL_VERBS;

/**
 * "Looked at a frame", "Looked at 3 frames", "Outlined 49 images with SAM 3.1". `frames` are the frames the runs
 * looked at; `images` is how many images a finished outline run returned, when it said.
 */
export function toolPhrase(tool: string, o: { running: boolean; frames: number[]; images?: number | null }): string {
  const [now, done] = TOOL_VERBS[tool];
  if (o.running) return now;
  if (tool === "inspect_video" && o.frames.length) {
    const n = new Set(o.frames).size;
    return n === 1 ? "Looked at a frame" : `Looked at ${n} frames`;
  }
  if (tool === "segment_images" && o.images) return `Outlined ${o.images} images with SAM 3.1`;
  return done;
}

/** Initials-free role names for the agents that took part, keyed by event actor. */
export function roleOf(actor: string): string | null {
  if (actor === "orchestrator") return "Planner";
  if (actor === "worker") return "Pointer";
  if (/segment|track/.test(actor)) return "Outliner";
  if (actor === "gate") return "Checker";
  if (actor === "critic") return "Second opinion";
  return null;
}
