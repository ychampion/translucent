"use client";
// The campaign at a glance: a small card at the top right of the conversation. Findings so far, who worked on it,
// what it cost. It never grows past a few lines; on a phone it folds into one tappable line.
import { useMemo, useState, type ReactNode } from "react";
import type { Verified } from "@/lib/campaigns";
import type { CampaignView, FindingState } from "@/lib/store";
import { StateMark } from "./Findings";
import { money, roleOf } from "@/lib/words";
import { IconAim, IconCheck, IconRoute, IconShape, IconChats } from "./Icons";

const ROLE_ORDER = ["Planner", "Pointer", "Outliner", "Checker", "Second opinion"];
const ROLE_NOTE: Record<string, string> = {
  Planner: "plans each step",
  Pointer: "points at things in the images",
  Outliner: "outlines what it’s shown",
  Checker: "fixed rules that decide every finding",
  "Second opinion": "gives advice only",
};
const ROLE_MARK: Record<string, ReactNode> = {
  Planner: <IconRoute size={12} />,
  Pointer: <IconAim size={12} />,
  Outliner: <IconShape size={12} />,
  Checker: <IconCheck size={12} />,
  "Second opinion": <IconChats size={12} />,
};
/** Who a role is: the model behind it, where one is. `who` names roles the run recorded. */
const roleWho = (r: string, model: string | null, who?: Record<string, string | null>) =>
  who?.[r] ?? (r === "Outliner" ? "SAM 3.1" : r === "Checker" ? null : model);

const TALLY_STATES: [FindingState, string][] = [["earned", "supported"], ["refused", "turned down"], ["not-proven", "open"], ["checking", "checking"]];

export function findingTally(view: CampaignView) {
  const n = (s: string) => view.findings.filter((f) => f.state === s).length;
  const parts: string[] = [];
  if (n("earned")) parts.push(`${n("earned")} supported`);
  if (n("refused")) parts.push(`${n("refused")} turned down`);
  if (n("not-proven")) parts.push(`${n("not-proven")} open`);
  if (n("checking")) parts.push(`${n("checking")} checking`);
  return parts.join(" · ");
}

export function Glance({ view, model, who, verified }: {
  view: CampaignView; model: string | null; who?: Record<string, string | null>; verified?: Verified;
}) {
  const [open, setOpen] = useState(false);
  const { roles, people, steps } = useMemo(() => {
    const roles = new Set<string>();
    const people = new Set<string>();
    let steps = 0;
    for (const e of view.events) {
      const r = roleOf(e.actor);
      if (r) roles.add(r);
      if (e.actor.startsWith("person:")) people.add(String(e.data?.author ?? e.actor.slice(7)));
      if (e.type === "tool.finished") steps += 1;
    }
    for (const f of view.findings) for (const p of f.people) if (p.author) people.add(p.author);
    return { roles: ROLE_ORDER.filter((r) => roles.has(r)), people: [...people], steps };
  }, [view.events, view.findings]);

  const tally = findingTally(view);
  const first = view.findings[0]?.claim.id;
  const jump = () => {
    if (!first) return;
    document.querySelector(`[data-finding="${CSS.escape(first)}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  if (!view.events.length) return null;
  const working = !view.finished;

  return (
    <section className={`glance ${open ? "is-open" : ""}`} aria-label="At a glance">
      <button type="button" className="glance-fold" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {tally || (working ? "Working" : "No findings")}
      </button>
      <div className="glance-body">
        <div className="glance-sec">
          <p className="glance-h">Findings</p>
          {tally ? (
            <button type="button" className="glance-line glance-tally is-link" onClick={jump}>
              {TALLY_STATES.map(([state, word]) => {
                const n = view.findings.filter((f) => f.state === state).length;
                return n ? <span key={state}><StateMark state={state} />{n} {word}</span> : null;
              })}
            </button>
          ) : <p className="glance-line soft">None yet</p>}
        </div>
        <div className="glance-sec">
          <p className="glance-h">{working ? "Working on it" : "Who worked on it"}</p>
          <p className="glance-line glance-tally">
            <span className="avatars">
              {roles.map((r) => {
                const name = roleWho(r, model, who);
                return (
                  <span key={r} className={`avatar role-${r.split(" ")[0].toLowerCase()}`} tabIndex={0} aria-label={`${r}${name ? `, ${name}` : ""}: ${ROLE_NOTE[r]}`}>
                    {ROLE_MARK[r]}
                    <span className="tip" role="tooltip"><b>{r}</b>{name && <span>{name}</span>}<span className="tip-soft">{ROLE_NOTE[r]}</span></span>
                  </span>
                );
              })}
              {people.map((p) => (
                <span key={p} className="avatar is-person" tabIndex={0} aria-label={p}>
                  {p.trim().charAt(0).toUpperCase()}
                  <span className="tip" role="tooltip"><b>{p}</b><span className="tip-soft">on the team</span></span>
                </span>
              ))}
            </span>
            <span className="soft num glance-steps">{steps} {steps === 1 ? "step" : "steps"}</span>
          </p>
        </div>
        {view.spentUsd != null && (
          <p className="glance-foot num">{money(view.spentUsd)}{view.budgetUsd != null ? ` of ${money(view.budgetUsd)}` : ""} spent</p>
        )}
        {verified && (
          <p className="glance-foot" title={verified.command}>Re-checked offline: {verified.matched} of {verified.findings} match</p>
        )}
      </div>
    </section>
  );
}
