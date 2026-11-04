// The recorded experiments the demo offers, in start-screen order. Pure data: imported by pages at build time and
// by the client.
import { withBase } from "./base";

/**
 * Folder under public/fixtures holding the Movie 4 replay export. Swap the export by changing this one name.
 * Now: `translucent export cmp_63ea5905a8 ...`, a live campaign on the meta profile run over the HTTP API with people
 * in the loop (Ana's note, her sign-off, Ben's challenge and the agent's corrected finding). Muse Wonder plans, Muse
 * Spark points, SAM 3.1 outlines live on the Meta Model API; Muse Spark Open gives the second opinion. m4-meta is the
 * earlier Meta run whose analysis findings rested on numbers typed into code (kept as evidence, not offered); m4-dev
 * is the dev-profile run.
 */
export const M4_FOLDER = "m4-team";

export interface Region { x: number; y: number; w: number; h: number }

export interface PaperWindow { kind: "short" | "recovery"; lo: number; hi: number }

/**
 * Movie 4's voltage events from the paper's on-screen voltage panel (data/groundtruth/reisecker2023.json,
 * `movie4_voltage_events`, hours on the experiment clock). The recording's clock starts at 41.32 h: its first image,
 * 41.3362 h (`movie4_timebase`), is at 58.3 s in the bundle.
 */
const M4_ZERO_H = 41.32;
const m4s = (h: number) => (h - M4_ZERO_H) * 3600;
const M4_WINDOWS: PaperWindow[] = [
  { kind: "short", lo: m4s(41.3439), hi: m4s(41.3458) }, // first short, voltage to 0 V: 86.0-92.9 s
  { kind: "recovery", lo: m4s(41.3787), hi: m4s(41.3807) }, // voltage leaves 0 V: 211.3-218.5 s
  { kind: "short", lo: m4s(41.4157), hi: m4s(41.4194) }, // second short: 345.2-357.8 s
];

export interface Experiment {
  key: string;
  /** Folder under public/fixtures with bundle.json and the video. */
  folder: string;
  name: string;
  source: string;
  /** Full-frame still, used as the video poster. */
  poster: string;
  /** Small still of the microscope view, for the start screen. */
  thumb: string;
  /** Part of the source video that is the microscope view, in source pixels. Omitted: the whole frame. */
  region?: Region;
  /** Part of the video to fill the start screen's picture with (source pixels). Omitted: `region`, else the frame. */
  startRegion?: Region;
  /** Opening question offered on the start screen when no recorded objective is available. */
  suggestion: string;
  /** Where "Start campaign" goes. */
  entry: "campaign" | "memory";
  /** Playback rate of the looping video on the start screen. */
  startRate: number;
  /** Size of the source video in pixels (start screen framing before the bundle loads). */
  videoSize: [number, number];
  /**
   * A value the source states, drawn as a quiet reference line on the voltage trace (seconds on the recording's
   * clock). Copy from the paper, not a result: results come from the bundle.
   */
  reference?: { t: number; label: string; source: string };
  /** One calm sentence under the measured-vs-stated comparison. */
  referenceNote?: string;
  /**
   * Time windows the paper's own data gives for moments on the recording's clock (seconds). Copied from the paper's
   * data, never from a result: whether a measured time falls inside one is computed on screen.
   */
  paper?: { source: string; windows: PaperWindow[] };
  /** Result of re-checking this export offline. */
  verified?: Verified;
  /**
   * Planner and pointer model ids of a replay recorded before runs named their models (campaign.started `models`).
   * Source them from the provider's billing ledger, never from memory.
   */
  models?: { planner: string; pointer: string };
}

/** What `translucent verify <folder>` printed for an export. Copied from a real run, never written by hand. */
export interface Verified {
  command: string;
  findings: number;
  matched: number;
  files: number;
  /** Date of the run this came from. */
  ranOn: string;
}

export interface LessonSource {
  label: string;
  /** Name under its picture on the memory screen. */
  short: string;
  source: string;
  thumb: string;
}

/**
 * Where a remembered lesson came from: by the campaign it was learned in (`learned_in`), else by its scope (design
 * fixtures). Campaign ids are from runs/fw-chain6 and runs/fw-chain8.
 */
export const LESSON_SOURCES: Record<string, LessonSource> = {
  cmp_9da57bba06: { label: "Learned on graphite", short: "Graphite", source: "Lu et al. 2023", thumb: "/posters/lu-thumb.jpg" },
  cmp_63ea5905a8: { label: "Learned on Movie 4", short: "Movie 4", source: "Reisecker et al. 2023", thumb: "/posters/m4-thumb.jpg" },
  cmp_326e542943: { label: "Learned on Movie 4", short: "Movie 4", source: "Reisecker et al. 2023", thumb: "/posters/m4-thumb.jpg" },
  cmp_d6e28c20b1: { label: "Learned on Movie 4", short: "Movie 4", source: "Reisecker et al. 2023", thumb: "/posters/m4-thumb.jpg" },
  // A later Movie 4 campaign (runs/fw-chain8) whose ledger the Lu replay below continued; not exported.
  cmp_92c5ca76f7: { label: "Learned on Movie 4", short: "Movie 4", source: "Reisecker et al. 2023", thumb: "/posters/m4-thumb.jpg" },
  // An earlier campaign on this same Lu movie, in the same ledger; not exported.
  cmp_468880e027: { label: "Learned on an earlier run of this movie", short: "Earlier run", source: "Lu et al. 2023", thumb: "/posters/lu-thumb.jpg" },
  "source:reisecker2023": { label: "Learned on Movie 4", short: "Movie 4", source: "Reisecker et al. 2023", thumb: "/posters/m4-thumb.jpg" },
};

export const lessonSource = (l: { learned_in: string; scope: string }): LessonSource | undefined =>
  LESSON_SOURCES[l.learned_in] ?? LESSON_SOURCES[l.scope];

export const EXPERIMENTS: Experiment[] = [
  {
    key: "m4",
    folder: M4_FOLDER,
    name: "Lithium filaments, solid-state cell",
    source: "Reisecker et al. 2023",
    poster: "/posters/m4.jpg",
    thumb: "/posters/m4-thumb.jpg",
    // Movie 4 is the microscope (left) beside the paper's own voltage plot (right). The stage shows the microscope;
    // the voltage is drawn by the app from the recorded log, under the video.
    region: { x: 50, y: 28, w: 820, h: 576 },
    startRegion: { x: 0, y: 0, w: 906, h: 1080 },
    suggestion:
      "How fast do the lithium filaments grow across the solid electrolyte, does the battery short when they bridge it, and does it recover when they retract?",
    entry: "campaign",
    startRate: 1,
    videoSize: [1920, 1080],
    // `uv run translucent verify web/public/fixtures/m4-team`, 2026-10-29: exit 0,
    // "12 findings re-checked, 12 match; 163 files intact". The run names its models in campaign.started.
    verified: { command: "translucent verify web/public/fixtures/m4-team", findings: 12, matched: 12, files: 163, ranOn: "2026-10-29" },
    paper: { source: "Reisecker et al., Nature Communications 2023", windows: M4_WINDOWS },
  },
  {
    key: "lu",
    // `translucent export cmp_9da57bba06 ...`: a live campaign on the meta profile over the HTTP API, started with the
    // team hero's Movie 4 lesson loaded. lu-meta (cmp_ce3a2aef27) is kept as evidence: its refutation rested on a number
    // typed into analysis code. The dev-profile export stays in lu-dev.
    folder: "lu-live",
    name: "Graphite electrode, fast charging",
    source: "Lu et al. 2023",
    poster: "/posters/lu.jpg",
    thumb: "/posters/lu-thumb.jpg",
    // Movie 1 carries the paper’s caption along the top (to y 80) and its own voltage plot in the lower right corner
    // (from x 838, y 578). The stage shows the full-width band between them; the app draws the voltage from the log.
    region: { x: 0, y: 84, w: 1280, h: 490 },
    startRegion: { x: 200, y: 84, w: 620, h: 490 },
    suggestion:
      "When does lithium start plating on the graphite electrode during fast charging, and does the cell's voltage log agree with the paper's stated onset after 10 h of charging?",
    entry: "memory",
    startRate: 1,
    videoSize: [1280, 938],
    // The paper states plating starts after 10 h of charging; cycle 1's charge starts 2 s into the log.
    reference: { t: 10 * 3600, label: "Stated onset", source: "Lu et al. 2023" },
    referenceNote: "The voltage first dips below 0 V well before the stated 10 h.",
    // `uv run translucent verify web/public/fixtures/lu-live`, 2026-10-29: exit 0,
    // "6 findings re-checked, 6 match; 189 files intact".
    verified: { command: "translucent verify web/public/fixtures/lu-live", findings: 6, matched: 6, files: 189, ranOn: "2026-10-29" },
  },
];

/**
 * A recording a lab added itself, or any experiment this app has no page for: followed live only. No crop, no paper
 * windows, no fixture; the video comes from the server's bundle. Not shown among the recorded runs.
 */
export const OWN: Experiment = {
  key: "own",
  folder: "",
  name: "Your recording",
  source: "Your lab",
  poster: "",
  thumb: "",
  suggestion: "",
  entry: "campaign",
  startRate: 1,
  videoSize: [1280, 720],
};

export const CAMPAIGN_KEYS = [...EXPERIMENTS, OWN].map((x) => x.key);

export function experiment(key: string): Experiment {
  return key === OWN.key ? OWN : EXPERIMENTS.find((x) => x.key === key) ?? EXPERIMENTS[0];
}

/**
 * The paper's window for a moment, and whether the measured time (value ± its uncertainty) overlaps it. Of several
 * windows of the same kind, the nearest one.
 */
export function paperMatch(ex: Experiment, kind: string, value: number, give: number | null | undefined) {
  const ws = (ex.paper?.windows ?? []).filter((w) => w.kind === kind);
  if (!ws.length) return null;
  const mid = (w: PaperWindow) => (w.lo + w.hi) / 2;
  const w = ws.reduce((a, b) => (Math.abs(mid(b) - value) < Math.abs(mid(a) - value) ? b : a));
  const u = give ?? 0;
  return { text: `Paper: ${Math.round(w.lo)}–${Math.round(w.hi)} s`, overlaps: value - u <= w.hi && value + u >= w.lo };
}

export const fixtureBase = (key: string) => withBase(`/fixtures/${experiment(key).folder}`);
