// What in a campaign is waiting on this person: open questions to the team, and supported findings they have not
// signed off or challenged yet. Sidebar badges, the tab title, the composer tray and the N key all read this.
import type { CampaignView, Finding, Question } from "./store";

export const openQuestions = (view: CampaignView): Question[] => view.questions.filter((q) => q.status === "open");

/** Supported findings (the latest of each retry chain) this person has not answered. Needs a name to know who "me" is. */
export function toSignOff(view: CampaignView, name: string | null | undefined): Finding[] {
  const me = (name ?? "").trim().toLowerCase();
  if (!me) return [];
  return view.findings.filter((f) => f.state === "earned" && !f.replacedBy && !f.people.some((p) => (p.author ?? "").toLowerCase() === me));
}

/** Questions plus findings to sign off. */
export function attentionCount(view: CampaignView, name: string | null | undefined): number {
  return openQuestions(view).length + toSignOff(view, name).length;
}
