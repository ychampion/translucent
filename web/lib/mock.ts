// DEMO / DEV ONLY. An in-browser stand-in for the API server, loaded only with `?mock=<scene>`. It replays a recorded
// campaign as if it were live and answers the team's actions locally, so every people-in-the-loop screen can be seen
// and screenshotted without a server. Nothing here is used in a normal page load (it is imported on demand).
//
// Scenes: mock=1 (a paced live run that asks the team a question), idle, question, paused, hiccup, challenged,
// signed (finished, with sign-offs), stopped. On the start screen, `?mock=1&add=1` opens "Add your own recording";
// uploads show progress, and a bad manifest is refused in the server's own words.
import type { Campaign, Event, Review } from "./contracts";
import { EXPERIMENTS } from "./campaigns";
import { PeopleError, setPeopleApi, seedIdentity, type Manifest, type PeopleApi, type RecordedExperiment } from "./people";
import { loadBundle, setLiveDriver, storeFrom } from "./sources";
import type { CampaignStore } from "./store";

export type Scene = "flow" | "idle" | "question" | "paused" | "hiccup" | "challenged" | "signed" | "stopped";
const SCENES: Scene[] = ["flow", "idle", "question", "paused", "hiccup", "challenged", "signed", "stopped"];
export const sceneOf = (v: string | null): Scene => (SCENES.includes(v as Scene) ? (v as Scene) : "flow");

const QUESTIONS: Record<string, { question: string; options: string[] }> = {
  m4: {
    question: "The second short is faint in the images. Should I lean on the voltage log for it?",
    options: ["Yes, use the log", "No, keep to the images"],
  },
  lu: {
    question: "The first charge dips below 0 V early. Should I treat that as the plating onset?",
    options: ["Yes", "No, wait for the images"],
  },
};

const CHALLENGE = "The voltage drop could be a contact problem, not a short.";
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

class MockRun {
  seq = 0;
  t = 0;
  pos = 0;
  paused = false;
  stopped = false;
  timer: ReturnType<typeof setTimeout> | null = null;
  openQuestion: string | null = null;
  asked = false;
  hiccuped = false;
  reviews = 0;

  constructor(public id: string, public key: string, public store: CampaignStore, public recorded: Event[], public scene: Scene) {}

  emit(type: string, actor: string, title: string, data: Record<string, unknown> = {}, refs: string[] = []): Event {
    const e = { seq: ++this.seq, campaign_id: this.id, t: (this.t += 2), type, actor, title, data, refs } as Event;
    this.store.ingest(e);
    return e;
  }

  replayOne() {
    const r = this.recorded[this.pos++];
    this.t = Math.max(this.t, r.t);
    this.store.ingest({ ...r, t: this.t, seq: ++this.seq, campaign_id: this.id });
  }

  /** Recorded events up to (not including) the campaign's end. */
  get body() { return this.recorded.findIndex((e) => e.type === "campaign.finished"); }

  ask() {
    const q = QUESTIONS[this.key] ?? QUESTIONS.m4;
    this.openQuestion = `q_${this.seq + 1}`;
    this.asked = true;
    this.emit("question.asked", "orchestrator", q.question, { question_id: this.openQuestion, question: q.question, options: q.options });
  }

  /** Earned findings so far, to sign off or challenge. */
  earned() { return this.store.getSnapshot().findings.filter((f) => f.state === "earned").map((f) => f.claim.id); }

  person(verdict: "agree" | "challenge", claimId: string, author: string, reason: string): Review {
    const review: Review = {
      id: `rev_person_${++this.reviews}`, claim_id: claimId, reviewer: "human", author,
      verdict: verdict === "agree" ? "supported" : "refuted", reason, created_at: this.t,
    };
    this.emit("claim.reviewed", `person:${author}`, verdict === "agree" ? "Signed off" : "Challenged", { review }, [claimId, review.id]);
    return review;
  }

  start() {
    const end = this.body < 0 ? this.recorded.length : this.body;
    if (this.scene === "flow") {
      while (this.pos < Math.min(6, end)) this.replayOne();
      this.tick();
      return;
    }
    while (this.pos < end) this.replayOne();
    const [a, b] = this.earned();
    switch (this.scene) {
      case "question": this.ask(); break;
      case "paused":
        this.emit("campaign.control", "person:Ana", "Paused", { action: "pause", author: "Ana" });
        this.emit("campaign.paused", "runtime", "Paused", { by: "Ana" });
        this.paused = true;
        break;
      case "hiccup":
        this.emit("campaign.retry", "runtime", "Retrying", { role: "orchestrator", attempt: 1, error: "timeout", wait_s: 4 });
        break;
      case "challenged":
        if (a) this.person("agree", a, "Ana", "");
        if (b) this.person("challenge", b, "Ben", CHALLENGE);
        break;
      case "signed":
      case "stopped":
        if (this.scene === "stopped") {
          this.emit("campaign.control", "person:Ana", "Stopped", { action: "stop", author: "Ana" });
          this.emit("campaign.finished", "runtime", "Campaign stopped", { status: "stopped" });
          break;
        }
        while (this.pos < this.recorded.length) this.replayOne();
        if (a) this.person("agree", a, "Ana", "");
        if (b) { this.person("agree", b, "Ana", ""); this.person("challenge", b, "Ben", CHALLENGE); }
        break;
    }
  }

  /** The paced run: one recorded step at a time; asks the team once, has one connection hiccup. */
  tick() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (this.paused || this.stopped || this.openQuestion) return;
    if (this.pos >= this.recorded.length) return;
    const share = this.pos / this.recorded.length;
    let ms = 650;
    if (!this.asked && share > 0.35) {
      this.ask();
      // Nobody answers within the wait: it carries on with what the records show.
      const id = this.openQuestion;
      setTimeout(() => {
        if (this.openQuestion !== id || this.stopped) return;
        this.openQuestion = null;
        this.emit("question.expired", "runtime", "No reply", { question_id: id });
        this.tick();
      }, 45000);
      return;
    }
    if (!this.hiccuped && share > 0.6) {
      this.hiccuped = true;
      this.emit("campaign.retry", "runtime", "Retrying", { role: "orchestrator", attempt: 1, error: "timeout", wait_s: 3 });
      ms = 3200;
    } else {
      this.replayOne();
    }
    this.timer = setTimeout(() => this.tick(), ms);
  }
}

const runs = new Map<string, MockRun>();
let counter = 0;

function run(id: string): MockRun {
  const r = runs.get(id);
  if (!r) throw new Error("No such demo campaign");
  return r;
}

/** What the server would refuse in a manifest, in its own words (translucent/experiments.py). */
function refusal(m: Manifest, list: RecordedExperiment[]): string | null {
  if (!/^[a-z0-9][a-z0-9_-]{2,63}$/.test(m.id)) return "id: String should match pattern '^[a-z0-9][a-z0-9_-]{2,63}$'";
  if (list.some((x) => x.experiment === `user/${m.id}`)) return `${m.id}: a different video is already registered under this id; use a new id`;
  const sc = m.scale;
  if (sc) {
    const value = sc.um_per_px ?? (sc.bar_um ?? 0) / (sc.bar_px ?? 1);
    if ((sc.uncertainty ?? 0) >= value) return "scale: scale uncertainty must be smaller than the scale in um per pixel";
  }
  const ck = m.clock;
  if (ck?.interval_s !== undefined && (ck.uncertainty_s ?? 0) >= ck.interval_s) return "clock: interval uncertainty must be smaller than the acquisition interval";
  return null;
}

export function installMock(scene: Scene) {
  seedIdentity("Ana", "demo");
  const recorded: RecordedExperiment[] = EXPERIMENTS.map((x) => ({ experiment: x.key, video_id: `art_${x.key}`, label: x.name, citation: x.source, licence: "CC BY 4.0" }));
  const uploads = new Map<string, Set<string>>();

  setLiveDriver({
    async open(id, key) {
      const b = await loadBundle(key);
      const recorded = [...b.events].sort((x, y) => x.seq - y.seq);
      const store = storeFrom({ ...b, events: [] }, false);
      runs.set(id, new MockRun(id, key, store, recorded, scene));
      return store;
    },
    connect(id) {
      const r = run(id);
      if (r.seq === 0) r.start();
      return () => {};
    },
  });

  const api: PeopleApi = {
    async session() { return { live: true, team: true }; },
    async jobStatus() { return null; },
    async experiments() { return [...recorded]; },
    async upload(uploadId, name, file, key, progress) {
      if (!key) throw new PeopleError(401, "That key didn’t work.");
      // About 40 MB/s, never under a second and a half, in visible steps.
      const steps = 12, ms = Math.max(1500, (file.size / 40e6) * 1000) / steps;
      for (let i = 1; i <= steps; i++) { await wait(ms); progress(Math.round((file.size * i) / steps)); }
      (uploads.get(uploadId) ?? uploads.set(uploadId, new Set()).get(uploadId)!).add(name);
      return { name, bytes: file.size };
    },
    async addExperiment(uploadId, manifest, key) {
      if (!key) throw new PeopleError(401, "That key didn’t work.");
      await wait(1200);
      const have = uploads.get(uploadId);
      if (!have) throw new PeopleError(404, "Upload the recording and its files first");
      const names = [manifest.video, manifest.clock?.timestamps_csv, manifest.voltage?.csv].filter((n): n is string => !!n);
      const lost = names.find((n) => !have.has(n));
      if (lost) throw new PeopleError(422, `${lost}: file not found; provide the recording and its CSV files`);
      const no = refusal(manifest, recorded);
      if (no) throw new PeopleError(422, no);
      const x = { experiment: `user/${manifest.id}`, video_id: `art_user_${manifest.id}`, label: manifest.title, citation: manifest.citation, licence: manifest.licence };
      recorded.push(x);
      uploads.delete(uploadId);
      return x;
    },
    async start(body) {
      await wait(500);
      const id = `demo_${++counter}`;
      return { id, objective: body.objective, title: body.objective.slice(0, 90), inputs: [body.experiment_id], budget_usd: body.budget, created_at: Date.now() / 1000 } as Campaign;
    },
    async note(id, text, author) {
      await wait(250);
      return run(id).emit("human.note", `person:${author}`, text.slice(0, 90), { text, author });
    },
    async answer(id, questionId, text, author) {
      await wait(250);
      const r = run(id);
      if (r.openQuestion !== questionId) throw new PeopleError(409, "That question has closed.");
      r.openQuestion = null;
      const e = r.emit("question.answered", `person:${author}`, text.slice(0, 90), { question_id: questionId, text, author });
      setTimeout(() => r.tick(), 900);
      return e;
    },
    async review(id, claimId, verdict, reason, author) {
      await wait(250);
      return run(id).person(verdict, claimId, author, reason);
    },
    async control(id, action, author) {
      await wait(250);
      const r = run(id);
      const e = r.emit("campaign.control", `person:${author}`, action, { action, author });
      if (action === "pause") { r.paused = true; r.emit("campaign.paused", "runtime", "Paused", { by: author }); }
      if (action === "resume") { r.paused = false; r.emit("campaign.resumed", "runtime", "Resumed", { by: author }); r.tick(); }
      if (action === "stop") { r.stopped = true; r.emit("campaign.finished", "runtime", "Campaign stopped", { status: "stopped" }); }
      return e;
    },
    async resume(id, budget) {
      await wait(400);
      const r = run(id);
      r.stopped = false;
      r.paused = false;
      r.emit("campaign.resumed", "runtime", "Resumed", { by: "admin" });
      r.scene = "flow";
      r.tick();
      return { id, objective: "", title: "", inputs: [], budget_usd: budget, created_at: Date.now() / 1000 } as Campaign;
    },
  };
  setPeopleApi(api);
}
