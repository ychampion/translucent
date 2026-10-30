// What changed between two campaigns, computed from their two bundles. Pure: no fetching, no model.
// Findings are paired by what they test (test type and cited quantities, a derived number by its unit), never by
// their wording, so two runs that phrase the same result differently still line up.
import type { Claim, Event, Measurement, ReplayBundle, Review } from "./contracts";
import { activeSeconds } from "./words";

export type DiffSide = Pick<ReplayBundle, "campaign" | "events"> & Partial<Pick<ReplayBundle, "claims" | "reviews" | "measurements">>;

export interface MeasurementChange {
  a: Measurement | null;
  b: Measurement | null;
  /** Both scalar and further apart than their uncertainties together. */
  moved: boolean;
}

export interface FindingPair {
  a: Claim;
  b: Claim;
  aVerdict: string;
  bVerdict: string;
  rangeChanged: boolean;
  measurements: MeasurementChange[];
  /** Nothing differs but the wording: same verdict, same range, no measurement moved. */
  same: boolean;
}

export type StepRow =
  | { kind: "same"; a: Event; b: Event }
  | { kind: "changed"; a: Event; b: Event }
  | { kind: "a"; a: Event }
  | { kind: "b"; b: Event };

export interface CampaignDiff {
  a: string;
  b: string;
  models: { a: Record<string, string>; b: Record<string, string> };
  toolCalls: { a: number; b: number; sameFirst: number; aNext: string | null; bNext: string | null };
  cost: { a: number; b: number };
  activeSeconds: { a: number; b: number };
  lessons: { a: string[]; b: string[] };
  findings: FindingPair[];
  onlyA: { claim: Claim; verdict: string }[];
  onlyB: { claim: Claim; verdict: string }[];
  steps: StepRow[];
  sentences: string[];
}

const ROLES: [string, string][] = [["planner", "Planner"], ["pointer", "Pointer"], ["second_opinion", "Second opinion"]];

function verdictOf(reviews: Review[], id: string): string {
  let v = "proposed";
  for (const r of reviews) if (r.claim_id === id && r.reviewer === "gate") v = r.verdict;
  return v;
}

const isDerived = (m: Measurement) => m.quantity.startsWith("derived:");
const qkey = (m: Measurement | undefined) => (m == null ? "?" : isDerived(m) ? `derived [${m.unit}]` : m.quantity);

function keyOf(c: Claim, ms: Map<string, Measurement>): string {
  return `${c.predicate.type}|${c.predicate.measurement_ids.map((i) => qkey(ms.get(i))).sort().join(",")}`;
}

function centre(c: Claim): number | null {
  const { lo, hi } = c.predicate.params as { lo?: unknown; hi?: unknown };
  return typeof lo === "number" && typeof hi === "number" ? (lo + hi) / 2 : null;
}

/** Two accepted ranges that could be about the same quantity: same sign, centres within half their size. */
function close(x: Claim, y: Claim): boolean {
  const cx = centre(x), cy = centre(y);
  if (cx == null || cy == null) return true;
  return cx * cy >= 0 && Math.abs(cx - cy) <= 0.5 * Math.max(Math.abs(cx), Math.abs(cy));
}

/** How many of the cited measurements' subjects the two findings share. */
function sharedSubjects(x: Claim, mx: Map<string, Measurement>, y: Claim, my: Map<string, Measurement>): number {
  const sx = new Set(x.predicate.measurement_ids.map((i) => mx.get(i)?.subject).filter(Boolean));
  return y.predicate.measurement_ids.filter((i) => sx.has(my.get(i)?.subject)).length;
}

/** Pairs findings that test the same kind of thing, nearest accepted range first; a latest version wins a tie. */
export function pairFindings(a: DiffSide, b: DiffSide): { pairs: [Claim, Claim][]; onlyA: Claim[]; onlyB: Claim[] } {
  const ca = a.claims ?? [], cb = b.claims ?? [];
  const ma = new Map((a.measurements ?? []).map((m) => [m.id, m])), mb = new Map((b.measurements ?? []).map((m) => [m.id, m]));
  const replacedA = new Set(ca.map((k) => k.supersedes).filter(Boolean)), replacedB = new Set(cb.map((k) => k.supersedes).filter(Boolean));
  const cand: { d: number; old: number; subj: number; i: number; j: number }[] = [];
  ca.forEach((x, i) => {
    const kx = keyOf(x, ma);
    cb.forEach((y, j) => {
      if (kx !== keyOf(y, mb) || !close(x, y)) return;
      const cx = centre(x), cy = centre(y);
      cand.push({
        d: cx != null && cy != null ? Math.abs(cx - cy) : 0,
        old: (replacedA.has(x.id) ? 1 : 0) + (replacedB.has(y.id) ? 1 : 0),
        subj: -sharedSubjects(x, ma, y, mb), i, j,
      });
    });
  });
  cand.sort((p, q) => p.d - q.d || p.old - q.old || p.subj - q.subj || p.i - q.i || p.j - q.j);
  const usedA = new Set<number>(), usedB = new Set<number>(), pairs: [number, number][] = [];
  for (const c of cand) {
    if (usedA.has(c.i) || usedB.has(c.j)) continue;
    usedA.add(c.i); usedB.add(c.j); pairs.push([c.i, c.j]);
  }
  pairs.sort((p, q) => p[0] - q[0]);
  return {
    pairs: pairs.map(([i, j]) => [ca[i], cb[j]]),
    onlyA: ca.filter((_, i) => !usedA.has(i)),
    onlyB: cb.filter((_, j) => !usedB.has(j)),
  };
}

/** Each cited measurement of A against the one of B that stands for the same quantity (and subject, when one does). */
export function matchMeasurements(xs: Measurement[], ys: Measurement[]): MeasurementChange[] {
  const left = [...ys];
  const key = (m: Measurement) => `${isDerived(m) ? "derived" : m.quantity}|${m.unit}`;
  const out: MeasurementChange[] = xs.map((x) => {
    const y = left.find((m) => key(m) === key(x) && m.subject === x.subject) ?? left.find((m) => key(m) === key(x)) ?? null;
    if (y) left.splice(left.indexOf(y), 1);
    return { a: x, b: y, moved: y != null && movedBeyond(x, y) };
  });
  return [...out, ...left.map((y) => ({ a: null, b: y, moved: false }))];
}

/** Two single numbers further apart than their uncertainties together. */
export function movedBeyond(x: Measurement, y: Measurement): boolean {
  if (typeof x.value !== "number" || typeof y.value !== "number" || x.unit !== y.unit) return false;
  return Math.abs(x.value - y.value) > (x.uncertainty?.value ?? 0) + (y.uncertainty?.value ?? 0);
}

const ID = /^(sha256:[0-9a-f]+|(m|run|claim|cmp|lesson|art|step|q)_[0-9a-f]+)$/;
/** A tool call's arguments with record ids blanked out: two campaigns never share ids, only what they ask for. */
export function stepKey(e: Event): string {
  const norm = (v: unknown): unknown =>
    typeof v === "string" ? (ID.test(v) ? "#" : v)
      : Array.isArray(v) ? v.map(norm)
        : v && typeof v === "object" ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, norm((v as Record<string, unknown>)[k])]))
          : v;
  return `${e.actor} ${JSON.stringify(norm(e.data?.args ?? {}))}`;
}

/** The two campaigns' tool calls side by side: a longest common run of tools, each pair same or changed. */
export function alignSteps(ta: Event[], tb: Event[]): StepRow[] {
  const n = ta.length, m = tb.length;
  const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = ta[i].actor === tb[j].actor ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const rows: StepRow[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (ta[i].actor === tb[j].actor) {
      rows.push({ kind: stepKey(ta[i]) === stepKey(tb[j]) ? "same" : "changed", a: ta[i], b: tb[j] });
      i++; j++;
    } else if (L[i + 1][j] >= L[i][j + 1]) rows.push({ kind: "a", a: ta[i++] });
    else rows.push({ kind: "b", b: tb[j++] });
  }
  while (i < n) rows.push({ kind: "a", a: ta[i++] });
  while (j < m) rows.push({ kind: "b", b: tb[j++] });
  return rows;
}

const money = (x: number) => `$${x.toFixed(2)}`;
const started = (s: DiffSide) => (s.events.find((e) => e.type === "campaign.started")?.data ?? {}) as Record<string, unknown>;
const cost = (s: DiffSide) => s.events.filter((e) => e.type === "cost.updated").reduce((t, e) => t + Number(e.data?.usd ?? 0), 0);
const lessons = (s: DiffSide) => [...new Set(s.events.filter((e) => e.type === "memory.loaded").flatMap((e) => e.refs ?? []))];
const tools = (s: DiffSide) => s.events.filter((e) => e.type === "tool.started").sort((x, y) => x.seq - y.seq);

export function diffCampaigns(a: DiffSide, b: DiffSide): CampaignDiff {
  const modelsA = (started(a).models ?? {}) as Record<string, string>, modelsB = (started(b).models ?? {}) as Record<string, string>;
  const ta = tools(a), tb = tools(b);
  const sameFirst = (() => { let k = 0; while (k < ta.length && k < tb.length && ta[k].actor === tb[k].actor) k++; return k; })();
  const aNext = ta[sameFirst]?.actor ?? null, bNext = tb[sameFirst]?.actor ?? null;
  const ra = a.reviews ?? [], rb = b.reviews ?? [];
  const ma = new Map((a.measurements ?? []).map((m) => [m.id, m])), mb = new Map((b.measurements ?? []).map((m) => [m.id, m]));
  const { pairs, onlyA, onlyB } = pairFindings(a, b);

  const findings: FindingPair[] = pairs.map(([x, y]) => {
    const xs = x.predicate.measurement_ids.map((i) => ma.get(i)).filter((m): m is Measurement => !!m);
    const ys = y.predicate.measurement_ids.map((i) => mb.get(i)).filter((m): m is Measurement => !!m);
    const measurements = matchMeasurements(xs, ys);
    const aVerdict = verdictOf(ra, x.id), bVerdict = verdictOf(rb, y.id);
    const rangeChanged = JSON.stringify(x.predicate.params) !== JSON.stringify(y.predicate.params);
    const same = aVerdict === bVerdict && !rangeChanged && measurements.every((c) => c.a && c.b && !c.moved);
    return { a: x, b: y, aVerdict, bVerdict, rangeChanged, measurements, same };
  });

  const sentences: string[] = [];
  for (const [role, word] of ROLES) {
    const x = modelsA[role], y = modelsB[role];
    if (x !== y) sentences.push(`${word}: ${x || "not recorded"} vs ${y || "not recorded"}.`);
  }
  if (aNext == null && bNext == null) sentences.push(`Both made the same ${ta.length} tool calls.`);
  else sentences.push(`Both began with the same ${sameFirst} tool calls; then A called ${aNext ?? "nothing more"} and B called ${bNext ?? "nothing more"}.`);
  const costA = cost(a), costB = cost(b);
  sentences.push(`Tool calls: ${ta.length} vs ${tb.length}. Model cost: ${money(costA)} vs ${money(costB)}.`);
  const la = lessons(a), lb = lessons(b);
  if (la.length || lb.length) sentences.push(`Lessons loaded at the start: ${la.length} vs ${lb.length}.`);
  const supported = (cs: Claim[], rs: Review[]) => cs.filter((k) => verdictOf(rs, k.id) === "supported").length;
  const ca = a.claims ?? [], cb = b.claims ?? [];
  sentences.push(`Findings: ${ca.length} proposed, ${supported(ca, ra)} supported vs ${cb.length} proposed, ${supported(cb, rb)} supported.`);

  return {
    a: a.campaign.id, b: b.campaign.id,
    models: { a: modelsA, b: modelsB },
    toolCalls: { a: ta.length, b: tb.length, sameFirst, aNext, bNext },
    cost: { a: costA, b: costB },
    activeSeconds: { a: activeSeconds(a.events.map((e) => e.t)), b: activeSeconds(b.events.map((e) => e.t)) },
    lessons: { a: la, b: lb },
    findings,
    onlyA: onlyA.map((claim) => ({ claim, verdict: verdictOf(ra, claim.id) })),
    onlyB: onlyB.map((claim) => ({ claim, verdict: verdictOf(rb, claim.id) })),
    steps: alignSteps(ta, tb),
    sentences,
  };
}

/** Runs of identical steps folded into one row each, so only the places that differ take room. */
export type StepGroup = { kind: "same"; rows: StepRow[] } | { kind: "differ"; row: StepRow };
export function groupSteps(rows: StepRow[]): StepGroup[] {
  const out: StepGroup[] = [];
  for (const r of rows) {
    const last = out[out.length - 1];
    if (r.kind === "same" && last?.kind === "same") last.rows.push(r);
    else out.push(r.kind === "same" ? { kind: "same", rows: [r] } : { kind: "differ", row: r });
  }
  return out;
}
