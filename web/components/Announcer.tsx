"use client";
// One quiet live region for screen readers: it announces only what changed that matters (a finding's verdict, a
// question for the team, the run pausing or stopping), never the whole thread as it grows.
import { useEffect, useRef, useState } from "react";
import type { CampaignView } from "@/lib/store";
import { STATE_WORD } from "@/lib/words";

const RUN_WORD: Record<string, string> = {
  running: "The run is going", paused: "The run is paused", stopped: "The run stopped",
  finished: "The campaign finished", provider_error: "The run stopped: the model could not be reached",
};

export function Announcer({ view }: { view: CampaignView }) {
  const [text, setText] = useState("");
  const seen = useRef<{ verdicts: Map<string, string>; questions: Set<string>; run: string | null; stream: string; events: number } | null>(null);
  useEffect(() => {
    const verdicts = new Map(view.findings.filter((f) => f.review || f.failed).map((f) => [f.claim.id, f.state] as const));
    const questions = new Set(view.questions.filter((q) => q.status === "open").map((q) => q.id));
    const prev = seen.current;
    seen.current = { verdicts, questions, run: view.runState, stream: view.stream, events: view.events.length };
    // The first snapshot, and a replay that jumped ahead or back, are history, not news.
    if (!prev || Math.abs(view.events.length - prev.events) > 8) return;
    const said: string[] = [];
    for (const f of view.findings) {
      const was = prev.verdicts.get(f.claim.id);
      if (verdicts.has(f.claim.id) && was !== f.state) said.push(`Finding ${STATE_WORD[f.state].toLowerCase()}: ${f.claim.statement.replace(/\.$/, "")}`);
    }
    for (const q of view.questions) if (questions.has(q.id) && !prev.questions.has(q.id)) said.push(`A question for the team: ${q.question}`);
    if (view.runState && view.runState !== prev.run && prev.run) said.push(RUN_WORD[view.runState]);
    if (view.stream !== prev.stream) said.push(view.stream === "ok" ? "Live feed back" : view.stream === "lost" ? "Lost the live feed" : "Reconnecting to the live feed");
    if (said.length) setText(said.join(". "));
  }, [view.findings, view.questions, view.runState, view.stream, view.events.length]);
  return <p className="visually-hidden" role="status" aria-live="polite">{text}</p>;
}
