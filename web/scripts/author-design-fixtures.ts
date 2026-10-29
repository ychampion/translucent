// Authors the DESIGN FIXTURES for the web UI: web/public/fixtures/{m1,lu}/events.json and lu/bundle.json.
// Run: bun run web/scripts/author-design-fixtures.ts   (from the repo root)
//
// These are a hand-written narrative for designing the UI, based on the real Day-1 story. They are replaced by
// real runtime replays. Rules followed here:
//   - Only real numbers: 50 tracked frames (60-109), 4.5 px median tracker agreement, 184.9 px/mm scale bar,
//     each image repeated 10 times (11 distinct images in 110 frames; verified by frame differencing and in
//     data/groundtruth/reisecker2023.json), "dendrite" / "thin dark line" text prompts returned 0 frames,
//     the paper’s stated 2-7 s between captured images.
//   - Numbers that do not exist yet (growth speed, cost, budget) are left out, and the UI shows "—".
//     Measurements referenced by findings but not present in `measurements` render as "—".
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Campaign, Check, Claim, Event, Lesson, Measurement, Review, Series } from "../lib/contracts";

const ROOT = join(import.meta.dir, "..", "..");
const FIX = join(ROOT, "web", "public", "fixtures");
const NOTE =
  "DESIGN FIXTURE. Hand-authored narrative over real data, for UI design only; replaced by real runtime replays. " +
  "Numbers not yet produced by the runtime (speeds, cost, budget) are intentionally absent and render as \"—\".";

// ------------------------------------------------------------------ helpers
function builder(campaign_id: string, t0: number) {
  const events: Event[] = [];
  let t = t0;
  return {
    events,
    add(dt: number, type: Event["type"], actor: string, title: string, extra: Partial<Event> = {}) {
      if (title.length > 90) throw new Error(`title over 90 chars: ${title}`);
      t += dt;
      events.push({ seq: events.length + 1, campaign_id, t: Math.round(t * 10) / 10, type, actor, title, ...extra });
      return t;
    },
  };
}

// ------------------------------------------------------------------ campaign 1: dendrite (Reisecker Movie 1)
const T0 = 1793170800; // 2026-10-28, design time only
const C1 = "c-dendrite-m1";
const campaign1: Campaign = {
  id: C1,
  objective: "How fast does the dendrite grow, does it speed up before the short, and when does the battery fail?",
  title: "Lithium dendrite, solid-state cell",
  inputs: ["artifact:reisecker2023-movie1"],
  memory: "on",
  budget_usd: 0, // not set yet; UI shows "—"
  created_at: T0,
};

const mAgree: Measurement = {
  id: "m-tracker-agreement", run_id: "run-classical-tip", quantity: "tip_agreement",
  value: 4.5, unit: "px", uncertainty: { value: null, kind: "none", method: "median over tracked frames" },
  subject: "dendrite 1", frame_range: [60, 109], label: "Two trackers agree on the tip",
};
const mScale: Measurement = {
  id: "m-scale", run_id: "run-scale-bar", quantity: "scale",
  value: 184.9, unit: "px/mm", uncertainty: { value: null, kind: "none", method: "from the paper’s 1 mm scale bar (Fig. 3a)" },
  label: "Scale",
};
const mFrames: Measurement = {
  id: "m-frames-tracked", run_id: "run-sam-point", quantity: "frames_tracked",
  value: 50, unit: "frames", uncertainty: { value: null, kind: "none", method: "" },
  subject: "dendrite 1", frame_range: [60, 109], label: "Frames tracked",
};
const mRepeat: Measurement = {
  id: "m-repeat", run_id: "run-dedupe", quantity: "frames_per_image",
  value: 10, unit: "frames", uncertainty: { value: null, kind: "none", method: "frame-to-frame difference" },
  frame_range: [0, 109], label: "Each image is shown",
};
const mImages: Measurement = {
  id: "m-distinct-images", run_id: "run-dedupe", quantity: "distinct_images",
  value: 5, unit: "images", uncertainty: { value: null, kind: "none", method: "" },
  frame_range: [60, 109], label: "Distinct images while tracked",
};
const mGap: Measurement = {
  id: "m-time-per-image", run_id: "run-paper", quantity: "time_per_image",
  value: [2, 7], unit: "s", uncertainty: { value: null, kind: "bound", method: "stated range in the paper’s methods" },
  label: "Time between images",
};
// Referenced but not yet produced by the runtime -> rendered as "—":
const SPEED_NAIVE = "m-speed-naive";
const SPEED = "m-speed-distinct";

const claims: Claim[] = [
  {
    id: "f-speed-1", campaign_id: C1, author: "worker",
    predicate: { type: "value_within", params: {}, measurement_ids: [SPEED_NAIVE, mScale.id] },
    statement: "The dendrite grows at a steady — µm/s.", created_at: 0,
  },
  {
    id: "f-speed-2", campaign_id: C1, author: "worker",
    predicate: { type: "value_within", params: {}, measurement_ids: [SPEED, mScale.id, mGap.id, mImages.id] },
    statement: "The dendrite grows at — µm/s, timed on distinct images.", supersedes: "f-speed-1", created_at: 0,
  },
  {
    id: "f-contact", campaign_id: C1, author: "worker",
    predicate: { type: "exists", params: { frame: 90 }, measurement_ids: [mAgree.id] },
    statement: "The dendrite reaches the other electrode in frame 90.", created_at: 0,
  },
  {
    id: "f-accel", campaign_id: C1, author: "worker",
    predicate: { type: "trend", params: { direction: "increasing" }, measurement_ids: [SPEED, mGap.id] },
    statement: "Growth speeds up just before the short.", created_at: 0,
  },
];

const chk = (name: string, passed: boolean, detail: string): Check => ({ name, passed, detail });
const reviews: Review[] = [
  {
    id: "r-speed-1", claim_id: "f-speed-1", reviewer: "gate", verdict: "unsupported", created_at: 0,
    reason: "The speed treats every frame as a new image, but this movie shows each image 10 times.",
    checks: [
      chk("ancestry", true, "Every tip position cites the frame it came from."),
      chk("units", true, "Pixels to millimetres uses the 1 mm scale bar."),
      chk("distinct_frames", false, "Frames 60–69 are one image shown 10 times."),
      chk("time_base", false, "Time came from the playback rate, not from when images were taken."),
    ],
  },
  {
    id: "r-speed-2", claim_id: "f-speed-2", reviewer: "gate", verdict: "supported", created_at: 0,
    reason: "Timed on the 5 distinct images, using the paper’s stated 2–7 s between images, carried as a range.",
    checks: [
      chk("ancestry", true, "Every tip position cites the frame it came from."),
      chk("units", true, "Pixels to millimetres uses the 1 mm scale bar."),
      chk("distinct_frames", true, "Timed on frames 60, 70, 80, 90 and 100 only."),
      chk("time_base", true, "Uses the paper’s stated 2–7 s between images."),
    ],
  },
  {
    id: "r-contact", claim_id: "f-contact", reviewer: "gate", verdict: "supported", created_at: 0,
    reason: "Both trackers put the tip at the far electrode from frame 90 on.",
    checks: [
      chk("ancestry", true, "Cites frames 80, 90 and 100."),
      chk("tracker_agreement", true, "An independent tracker agrees within 4.5 px (median)."),
      chk("in_frame", true, "The tip is visible at the electrode edge."),
    ],
  },
  {
    id: "r-accel", claim_id: "f-accel", reviewer: "gate", verdict: "inconclusive", created_at: 0,
    reason: "This movie doesn’t record when each image was taken, so a speed-up can’t be told apart from timing.",
    checks: [
      chk("distinct_frames", true, "Uses the 5 distinct images."),
      chk("time_base", false, "Only a 2–7 s range is stated; a speed-up needs the time of each image."),
      chk("enough_points", false, "5 images is too few to test a trend."),
    ],
  },
];

const lessons: Lesson[] = [
  {
    id: "l-repeats", text: "These movies repeat each image. Remove repeats before timing growth.",
    scope: "source:reisecker2023", evidence_ids: ["f-speed-1", "r-speed-1", mRepeat.id], learned_in: C1, created_at: 0,
  },
  {
    id: "l-point", text: "Point at the object to track it. Naming it doesn’t work on microscopy.",
    scope: "tool:sam-3.1", evidence_ids: ["run-sam-text", "run-sam-point"], learned_in: C1, created_at: 0,
  },
  {
    id: "l-scale", text: "Measure scale from the scale bar, not the stated field of view.",
    scope: "modality:optical-operando", evidence_ids: [mScale.id], learned_in: C1, created_at: 0,
  },
];

const byId = <T extends { id: string }>(xs: T[], id: string) => xs.find((x) => x.id === id)!;
const cost = { spent_usd: null, budget_usd: null };

const b1 = builder(C1, T0);
const stamp = (t: number, o: { created_at: number }) => { o.created_at = t; };
b1.add(0, "campaign.started", "orchestrator", "Started: how fast does the dendrite grow, and when does the battery fail?", { data: { campaign: campaign1 } });
b1.add(1.2, "memory.loaded", "runtime", "No earlier lessons. This is the first campaign.", { data: { lessons: [] } });
b1.add(3.8, "step.started", "orchestrator", "Plan: track the dendrite, follow its tip, time the growth");
b1.add(2.1, "tool.started", "sam", "Looking for the dendrite by name", { data: { prompt: "dendrite" } });
b1.add(6.1, "tool.finished", "sam", "Nothing found by name", {
  detail: "Asked for \"dendrite\" and \"thin dark line\". Neither matched any of the 110 frames.",
  refs: ["run-sam-text"], data: { frames_returned: 0 },
});
b1.add(1.6, "cost.updated", "runtime", "Cost updated", { data: cost });
b1.add(2.4, "tool.started", "sam", "Pointing at the dendrite instead", { data: { frames: [60] } });
b1.add(9.5, "tool.finished", "sam", "Dendrite tracked in 50 frames", {
  refs: ["run-sam-point", mFrames.id], data: { frame_range: [60, 109], track: "dendrite 1", measurement: mFrames },
});
b1.add(0.8, "cost.updated", "runtime", "Cost updated", { data: cost });
b1.add(3.2, "measurement.recorded", "measure", "A second, independent tracker agrees within 4.5 px", {
  refs: [mAgree.id], data: { measurement: mAgree, frame_range: [60, 109] },
});
b1.add(2.2, "measurement.recorded", "measure", "Scale set from the paper’s 1 mm scale bar: 184.9 px per mm", {
  refs: [mScale.id], data: { measurement: mScale },
});
b1.add(2.6, "measurement.recorded", "measure", "Growth speed measured from the tip in every frame", {
  refs: [SPEED_NAIVE], data: { frame_range: [60, 109] },
});
let t = b1.add(1.9, "claim.proposed", "worker", "Finding: the dendrite grows at a steady speed", {
  refs: ["f-speed-1"], data: { claim: byId(claims, "f-speed-1"), frames: [60, 61, 62, 63, 64, 65, 66, 67, 68, 69] },
});
stamp(t, byId(claims, "f-speed-1"));
t = b1.add(4.4, "claim.reviewed", "gate", "Refused: the speed counts repeated frames as new images", {
  refs: ["f-speed-1", "r-speed-1"], data: { review: byId(reviews, "r-speed-1") },
});
stamp(t, byId(reviews, "r-speed-1"));
b1.add(3.0, "step.started", "orchestrator", "Test: compare neighbouring frames to find repeats");
b1.add(1.4, "tool.started", "sandbox", "Comparing each frame with the one before");
b1.add(5.2, "tool.finished", "sandbox", "Each image is shown 10 times. 110 frames hold 11 real images.", {
  refs: ["run-dedupe", mRepeat.id], data: { measurement: mRepeat, distinct_frames: [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100] },
});
b1.add(2.0, "measurement.recorded", "measure", "Speed re-measured on the 5 distinct images", {
  refs: [SPEED, mImages.id, mGap.id], data: { measurement: mImages, frames: [60, 70, 80, 90, 100] },
});
t = b1.add(1.8, "claim.proposed", "worker", "Finding: the dendrite grows at a measured speed, timed on distinct images", {
  refs: ["f-speed-2"], data: { claim: byId(claims, "f-speed-2"), frames: [60, 70, 80, 90, 100] },
});
stamp(t, byId(claims, "f-speed-2"));
t = b1.add(4.1, "claim.reviewed", "gate", "Earned: the speed holds up on distinct images", {
  refs: ["f-speed-2", "r-speed-2"], data: { review: byId(reviews, "r-speed-2") },
});
stamp(t, byId(reviews, "r-speed-2"));
t = b1.add(2.7, "claim.proposed", "worker", "Finding: the dendrite reaches the other electrode in frame 90", {
  refs: ["f-contact"], data: { claim: byId(claims, "f-contact"), frames: [80, 90, 100] },
});
stamp(t, byId(claims, "f-contact"));
t = b1.add(3.6, "claim.reviewed", "gate", "Earned: both trackers put the tip at the electrode from frame 90", {
  refs: ["f-contact", "r-contact"], data: { review: byId(reviews, "r-contact") },
});
stamp(t, byId(reviews, "r-contact"));
t = b1.add(2.3, "claim.proposed", "worker", "Finding: growth speeds up just before the short", {
  refs: ["f-accel"], data: { claim: byId(claims, "f-accel"), frames: [60, 70, 80, 90] },
});
stamp(t, byId(claims, "f-accel"));
b1.add(1.5, "step.started", "orchestrator", "Test: time each step between images");
t = b1.add(3.9, "claim.reviewed", "gate", "Not proven yet: this movie doesn’t record when each image was taken", {
  refs: ["f-accel", "r-accel"], data: { review: byId(reviews, "r-accel") },
});
stamp(t, byId(reviews, "r-accel"));
for (const l of lessons) {
  t = b1.add(1.6, "memory.learned", "orchestrator", `Learned: ${l.text}`, { refs: [l.id], data: { lesson: l } });
  stamp(t, l);
}
b1.add(1.2, "campaign.finished", "orchestrator", "Finished: 2 findings earned, 1 not proven yet", {
  data: { answer: "The dendrite crossed the electrolyte in five captured images and reached the other electrode in frame 90. Its speed holds up once repeated frames are removed. Whether it speeds up before the short can’t be told from this movie." },
});

const m1 = {
  note: NOTE,
  campaign: campaign1,
  events: b1.events,
  measurements: [mFrames, mAgree, mScale, mRepeat, mImages, mGap],
  claims,
  reviews,
  lessons,
};
writeFileSync(join(FIX, "m1", "events.json"), JSON.stringify(m1, null, 1));

// ------------------------------------------------------------------ campaign 2: graphite (Lu Movie 1), memory moment
const T1 = T0 + 86400;
const C2 = "c-graphite-lu";
const campaign2: Campaign = {
  id: C2,
  objective: "As the graphite charges, when does lithium start plating on its surface?",
  title: "Graphite electrode, charging",
  inputs: ["artifact:lu2023-movie1"],
  memory: "on",
  budget_usd: 0,
  created_at: T1,
};
const loaded = lessons.map((l) => ({ ...l }));
const b2 = builder(C2, T1);
b2.add(0, "campaign.started", "orchestrator", "Started: when does lithium start plating on the graphite?", { data: { campaign: campaign2 } });
b2.add(1.4, "memory.loaded", "runtime", "Loaded 3 lessons from the dendrite campaign", { refs: loaded.map((l) => l.id), data: { lessons: loaded } });
b2.add(3.1, "step.started", "orchestrator", "Plan: check for repeated images before timing anything", { data: { lesson_ids: ["l-repeats"] } });
b2.add(1.2, "tool.started", "sandbox", "Comparing each frame with the one before", { data: { lesson_ids: ["l-repeats"] } });
b2.add(2.5, "step.started", "orchestrator", "Plan: point at particles to track them, not name them", { data: { lesson_ids: ["l-point"] } });
b2.add(1.8, "step.started", "orchestrator", "Plan: set scale from the scale bar", { data: { lesson_ids: ["l-scale"] } });

// Lu Fig. 2 voltage (real Source Data), cycle 1 only (charge, rest, discharge, rest), downsampled for display.
const csv = readFileSync(join(ROOT, "data", "groundtruth", "lu2023", "lu2023_fig2_voltage.csv"), "utf8").trim().split(/\r?\n/);
const head = csv[0].split(",");
const iCycle = head.indexOf("cycle"), iT = head.indexOf("t_s"), iV = head.indexOf("voltage_V_vs_Li");
const rows = csv.slice(1).map((l) => l.split(",")).filter((r) => r[iCycle] === "1");
const step = Math.ceil(rows.length / 600);
const series: Series = { name: "Cell voltage", unit: "V", t: [], y: [], frame_of_t: null };
rows.forEach((r, i) => {
  if (i % step === 0 || i === rows.length - 1) { series.t.push(+r[iT]); series.y.push(+r[iV]); }
});

const lu = { note: NOTE, campaign: campaign2, events: b2.events, measurements: [], claims: [], reviews: [], lessons: loaded };
writeFileSync(join(FIX, "lu", "events.json"), JSON.stringify(lu, null, 1));
writeFileSync(join(FIX, "lu", "bundle.json"), JSON.stringify({
  contract_version: "1",
  note: "Design fixture: Lu et al. Movie 1 cropped to remove burned-in text and plot (800x848 from x=20,y=90). " +
    "Voltage is the paper’s real Fig. 2 Source Data, cycle 1; it is not yet aligned to video time (frame_of_t null).",
  video: "movie.mp4", video_size: [800, 848], fps: 30,
  source: { citation: "Lu et al., Nat. Commun. 14, 5127 (2023), Supplementary Movie 1", licence: "CC BY 4.0" },
  tracks: [], series: [series],
}));
console.log(`m1: ${b1.events.length} events; lu: ${b2.events.length} events, ${series.t.length} voltage points`);
