// TypeScript mirror of translucent/contracts.py (CONTRACT_VERSION "1").
// Source of truth: translucent/contracts.py and docs/replay-bundle.schema.json. Keep field names identical.
// Fields with a default on the Python side are optional here (they may be omitted in JSON).

export const CONTRACT_VERSION = "1";

// ---------------------------------------------------------------- evidence

export type ArtifactKind =
  | "video" | "frame" | "masks" | "overlay" | "series" | "table" | "code" | "plot" | "text" | "report";

export interface Source {
  uri: string;
  citation?: string | null;
  licence?: string | null;
}

export interface Transform {
  op: string;
  parent_ids: string[];
  params?: Record<string, unknown>;
}

export interface Artifact {
  id: string;
  kind: ArtifactKind;
  media_type: string;
  bytes: number;
  shape?: number[] | null;
  axes?: string[] | null;
  source?: Source | null;
  transform?: Transform | null;
  label?: string | null;
  created_at: number;
}

export type RunStatus = "ok" | "tool_failure" | "invalid_result";

export interface Run {
  id: string;
  campaign_id: string;
  step_id: string;
  tool: string;
  idempotency_key: string;
  input_ids: string[];
  output_ids?: string[];
  /** Measurements this run produced (null: records made outside a tool call). */
  measurement_ids?: string[] | null;
  params?: Record<string, unknown>;
  code_sha?: string | null;
  prompt_sha?: string | null;
  provider?: string | null;
  model?: string | null;
  provider_request_id?: string | null;
  cost_usd?: number;
  status: RunStatus;
  error?: string | null;
  started_at: number;
  ended_at: number;
}

export type UncertaintyKind = "std" | "ci95" | "bound" | "resolution" | "none";

export interface Uncertainty {
  value: number | null;
  kind: UncertaintyKind;
  method: string;
}

export interface Measurement {
  id: string;
  run_id: string;
  quantity: string;
  value: number | number[];
  unit: string;
  uncertainty: Uncertainty;
  subject?: string | null;
  frame_range?: [number, number] | null;
  time_range?: [number, number] | null;
  calibration_ids?: string[];
  artifact_ids?: string[];
  valid_domain?: string | null;
  label?: string | null;
}

export type PredicateType = "value_within" | "values_agree" | "events_coincide" | "trend" | "exists";

export interface Predicate {
  type: PredicateType;
  params: Record<string, unknown>;
  measurement_ids: string[];
}

export type ClaimStatus = "proposed" | "supported" | "refuted" | "inconclusive" | "unsupported" | "stale";

export interface Claim {
  id: string;
  campaign_id: string;
  author: string;
  predicate: Predicate;
  statement: string;
  supersedes?: string | null;
  created_at: number;
}

export interface Check {
  name: string;
  passed: boolean;
  detail: string;
}

export type Verdict = "supported" | "refuted" | "inconclusive" | "unsupported";

export interface Review {
  id: string;
  claim_id: string;
  reviewer: "gate" | "critic" | "human";
  /** A person's name, on a human review ("I agree" is supported, a challenge is refuted). */
  author?: string | null;
  verdict: Verdict;
  checks?: Check[];
  reason: string;
  artifact_ids?: string[];
  model?: string | null;
  created_at: number;
}

export interface Lesson {
  id: string;
  text: string;
  scope: string;
  evidence_ids: string[];
  learned_in: string;
  created_at: number;
}

// ---------------------------------------------------------------- campaign + event stream

export interface Campaign {
  id: string;
  objective: string;
  title: string;
  inputs: string[];
  memory?: "on" | "off";
  budget_usd: number;
  created_at: number;
}

export type EventType =
  | "campaign.started" | "campaign.finished"
  | "memory.loaded" | "memory.learned"
  | "step.started" | "step.finished"
  | "tool.started" | "tool.finished"
  | "artifact.created" | "measurement.recorded"
  | "claim.proposed" | "claim.reviewed"
  | "cost.updated"
  // people in the loop
  | "human.note"
  | "question.asked" | "question.answered" | "question.expired"
  | "campaign.control" | "campaign.paused" | "campaign.resumed" | "campaign.retry";

export interface Event {
  seq: number;
  campaign_id: string;
  t: number;
  type: EventType;
  actor: string;
  /** Plain language for the viewer, at most 90 characters. */
  title: string;
  detail?: string | null;
  refs?: string[];
  data?: Record<string, unknown>;
}

// ---------------------------------------------------------------- replay bundle

export interface Track {
  subject: string;
  frames: number[];
  /** SVG path data per frame, in mask-local pixels: translate by mask_origins[i]. */
  mask_paths: string[];
  /** Top-left (x, y) of each mask in source-video pixels. */
  mask_origins: [number, number][];
  points?: ([number, number] | null)[];
}

export interface Series {
  name: string;
  unit: string;
  t: number[];
  y: number[];
  frame_of_t?: number[] | null;
}

export interface ReplayBundle {
  contract_version?: string;
  campaign: Campaign;
  video: string;
  video_size: [number, number];
  fps: number;
  frame_time_s?: number[] | null;
  tracks?: Track[];
  series?: Series[];
  events: Event[];
  artifacts?: Artifact[];
  measurements?: Measurement[];
  claims?: Claim[];
  reviews?: Review[];
  lessons?: Lesson[];
}
