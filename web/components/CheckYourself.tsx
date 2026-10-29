"use client";
import { useState } from "react";
import type { Verified } from "@/lib/campaigns";
import { CheckGlyph } from "./Findings";

/** Every finding can be re-checked offline: one command, and what it said when we ran it. */
export function CheckYourself({ v, className }: { v: Verified; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(v.command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard blocked (e.g. insecure context): the command stays selectable.
    }
  };
  const all = v.matched === v.findings;
  return (
    <section className={`check-yourself ${className ?? ""}`} aria-label="Check it yourself">
      <h2 className="detail-h">Check it yourself</h2>
      <p className="cy-lead">Re-run every check and re-hash every file, offline. No account, no cloud.</p>
      <div className="cy-command">
        <code>{v.command}</code>
        <button type="button" className="cy-copy" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
      </div>
      <p className="cy-result num">
        <span><CheckGlyph ok={all} /> {v.matched} of {v.findings} findings match</span>
        <span><CheckGlyph ok /> {v.files} recorded files unchanged</span>
      </p>
    </section>
  );
}
