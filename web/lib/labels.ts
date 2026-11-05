// Display words for the agent's own working names. Nothing recorded changes: these only spell out what is shown.

/**
 * A measurement's label as a person would say it. The agent names its working numbers tersely ("Late slope new1
 * last3", "Early growth speed lane y467 tight box"); this spells the abbreviations out and keeps every distinction.
 */
export function humanLabel(label: string): string {
  let s = label
    .replace(/\bWindow slope\b/i, "Early slope")
    .replace(/\s*\b(?:filament )?(?:in )?lane(?: near)? y ?(\d+)\b|\s*\blane(\d+)\b/gi, (_, a, b) => `, filament at y ${a ?? b} px`)
    .replace(/\b(of|for),/g, "$1")
    .replace(/\s*\bnew, (filament at y \d+ px) outline\b/i, ", $1, redrawn outline")
    .replace(/\s*\btight box\b/i, ", tight outline")
    .replace(/\bobj(\d+)\b/gi, "object $1")
    .replace(/\bs(\d+)([a-z]?)\b/g, (_, n: string, x: string) => `series ${n}${x}`)
    .replace(/\bnew(\d+)\b/g, "new series $1")
    .replace(/\b(first|last)(\d+)\b/gi, (_, w: string, n: string) => `${w.toLowerCase()} ${n} points`)
    .replace(/^check (.*)$/i, "Re-check: $1");
  // ", first 3 points": a window reads as an aside to what is measured
  s = s.replace(/\s*,?\s+\(?((?:first|last) \w+(?: credible)? points)\)?/gi, ", $1");
  s = s.replace(/\s+,/g, ",").replace(/,(\s*,)+/g, ",").replace(/\s{2,}/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}
