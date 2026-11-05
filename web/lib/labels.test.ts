import { expect, test } from "bun:test";
import { humanLabel } from "./labels";

test("the agent's working names read as words", () => {
  const cases: [string, string][] = [
    ["Early growth speed lane y467 tight box", "Early growth speed, filament at y 467 px, tight outline"],
    ["Window slope s2c first3", "Early slope series 2c, first 3 points"],
    ["Late slope new1 last3", "Late slope new series 1, last 3 points"],
    ["Median filament length new lane y467 outline", "Median filament length, filament at y 467 px, redrawn outline"],
    ["check early speed lane467", "Re-check: early speed, filament at y 467 px"],
    ["Early growth speed of filament in lane near y 467 (first five points)", "Early growth speed of filament at y 467 px, first five points"],
    ["Growth speed, object 1", "Growth speed, object 1"],
    ["Image scale", "Image scale"],
    ["Early slope first eight points object 4 second series", "Early slope, first eight points object 4 second series"],
  ];
  for (const [raw, want] of cases) expect(humanLabel(raw)).toBe(want);
});
