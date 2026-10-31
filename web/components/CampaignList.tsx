"use client";
// Campaigns on the server, one ledger at a time (/campaigns/?ledger=<lid>): newest first, each with its state, what
// it waits for, its outcome and spend. Loop campaigns open the loop screen. `ServerCampaigns` is the short version
// Home shows above the recorded examples.
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, v1 } from "@/lib/api";
import {
  ago, KIND_WORD, loopHref, NEXT_WORD, objectiveLine, OUTCOME_WORD, STATUS_WORD,
  type CampaignSummary, type LedgerInfo,
} from "@/lib/loop";
import { apiConfigured } from "@/lib/sources";
import { PageHeader } from "./Shell";
import { LoadState } from "./Status";

const LEDGER_KEY = "translucent.ledger";

function rememberedLedger(): string {
  try { return localStorage.getItem(LEDGER_KEY) ?? ""; } catch { return ""; }
}
function rememberLedger(lid: string) {
  try { localStorage.setItem(LEDGER_KEY, lid); } catch { /* this tab only */ }
}

/** The server's ledgers, and the one picked: ?ledger=, else the last one used here, else the first. */
function useLedgers() {
  const [ledgers, setLedgers] = useState<LedgerInfo[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const [lid, setLid] = useState("");
  useEffect(() => {
    const ctl = new AbortController();
    setError(null);
    v1.ledgers(ctl.signal).then((ls) => {
      setLedgers(ls);
      const asked = new URLSearchParams(window.location.search).get("ledger") ?? "";
      const pick = [asked, rememberedLedger()].find((x) => x && ls.some((l) => l.id === x)) ?? ls[0]?.id ?? "";
      setLid(pick);
    }).catch((e) => { if (!ctl.signal.aborted) setError(e); });
    return () => ctl.abort();
  }, [attempt]);
  const choose = useCallback((id: string) => {
    setLid(id);
    rememberLedger(id);
    const q = new URLSearchParams(window.location.search);
    q.set("ledger", id);
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${q}`);
  }, []);
  return { ledgers, error, lid, choose, retry: () => setAttempt((n) => n + 1) };
}

/** One ledger's campaigns, a page at a time. */
function useCampaigns(lid: string) {
  const [items, setItems] = useState<CampaignSummary[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [more, setMore] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!lid) return;
    const ctl = new AbortController();
    setItems(null);
    setError(null);
    v1.campaigns(lid, null, ctl.signal).then((p) => { setItems(p.items); setNext(p.next_cursor); })
      .catch((e) => { if (!ctl.signal.aborted) setError(e); });
    return () => ctl.abort();
  }, [lid, attempt]);
  const loadMore = async () => {
    if (next == null) return;
    setMore(true);
    try {
      const p = await v1.campaigns(lid, next);
      setItems((xs) => [...(xs ?? []), ...p.items]);
      setNext(p.next_cursor);
    } catch (e) {
      setError(e);
    } finally {
      setMore(false);
    }
  };
  return { items, next, error, more, loadMore, retry: () => setAttempt((n) => n + 1) };
}

export function CampaignList() {
  useEffect(() => { document.title = "Campaigns – Translucent"; }, []);
  const { ledgers, error, lid, choose, retry } = useLedgers();
  if (!ledgers) {
    return (
      <div className="screen loading">
        <PageHeader title="Campaigns" />
        {error && !apiConfigured() ? <p className="lp-plain">No team server is configured for this page (NEXT_PUBLIC_API_BASE).</p> : null}
        <LoadState error={error} what="the server’s ledgers" onRetry={retry} />
      </div>
    );
  }
  const current = ledgers.find((l) => l.id === lid);
  return (
    <div className="screen">
      <PageHeader title="Campaigns" />
      <main className="lp-main cl-main">
        <header className="cl-head">
          <h1 className="lp-statement">Campaigns on the server</h1>
          <p className="lp-plain">Each ledger is one team’s evidence. Pick one to see its campaigns; open a loop campaign to see the agent’s next experiment and decide on it.</p>
        </header>
        {ledgers.length === 0 ? (
          <p className="lp-plain">This server has no ledgers yet. Create one with <code>translucent init</code>.</p>
        ) : (
          <div className="iv-source" role="tablist" aria-label="Ledger">
            {ledgers.map((l) => (
              <button key={l.id} role="tab" aria-selected={l.id === lid} className={`iv-source-tab ${l.id === lid ? "is-on" : ""}`} onClick={() => choose(l.id)}>
                {l.title || l.id}<span className="iv-source-kind num">{l.campaigns}</span>
              </button>
            ))}
          </div>
        )}
        {current && <Ledger lid={current.id} />}
        <p className="lp-plain">Have a recorded export? <Link className="text-link" href="/w/">Open a .loop.json file</Link>.</p>
      </main>
    </div>
  );
}

function Ledger({ lid }: { lid: string }) {
  const { items, next, error, more, loadMore, retry } = useCampaigns(lid);
  if (!items) return <LoadState error={error} what="this ledger’s campaigns" onRetry={retry} />;
  if (!items.length) return <p className="lp-plain">No campaigns in this ledger yet.</p>;
  return (
    <>
      <CampaignRows items={items} />
      {error instanceof ApiError ? <p className="lp-error" role="alert">{error.message}</p> : null}
      {next != null && <button className="btn btn-quiet btn-small" onClick={loadMore} disabled={more}>{more ? "Loading…" : "Show more"}</button>}
    </>
  );
}

function CampaignRows({ items, compact }: { items: CampaignSummary[]; compact?: boolean }) {
  const now = Date.now() / 1000;
  return (
    <ul className={`cl-list ${compact ? "is-compact" : ""}`}>
      {items.map((c) => {
        const body = (
          <>
            <span className="cl-top">
              <span className="cl-title">{c.title}</span>
              {c.outcome ? <span className={`lp-outcome is-${c.outcome}`}>{OUTCOME_WORD[c.outcome]}</span>
                : <span className={`cl-status is-${c.status}`}>{STATUS_WORD[c.status] ?? c.status}</span>}
            </span>
            {!compact && c.objective && c.objective !== c.title && <span className="cl-objective">{objectiveLine(c.objective)}</span>}
            <span className="cl-meta">
              {c.next_action !== "none" && NEXT_WORD[c.next_action] ? <span className={`cl-next is-${c.next_action}`}>{NEXT_WORD[c.next_action]}</span> : null}
              <span>{KIND_WORD[c.kind] ?? c.kind}</span>
              <span className="num">${c.spend_usd.toFixed(2)}</span>
              <span>updated {ago(now - c.updated_at)}</span>
            </span>
          </>
        );
        return (
          <li key={`${c.ledger}/${c.id}`}>
            {c.kind === "loop" ? <Link className="cl-row" href={loopHref(c.ledger, c.id)}>{body}</Link>
              : <div className="cl-row is-static" title="This kind of campaign opens from its recording, not here">{body}</div>}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Home's list: the newest loop campaigns of every ledger on the server, when one is configured. Renders nothing
 * without a server (the recorded examples stand alone then).
 */
export function ServerCampaigns({ limit = 4 }: { limit?: number }) {
  const [items, setItems] = useState<CampaignSummary[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!apiConfigured()) return;
    const ctl = new AbortController();
    (async () => {
      const ledgers = await v1.ledgers(ctl.signal);
      const pages = await Promise.all(ledgers.map((l) => v1.campaigns(l.id, null, ctl.signal)));
      const all = pages.flatMap((p) => p.items).filter((c) => c.kind === "loop").sort((a, b) => b.updated_at - a.updated_at);
      setItems(all.slice(0, limit));
    })().catch((e) => { if (!ctl.signal.aborted) { console.warn("Server campaigns", e); setFailed(true); } });
    return () => ctl.abort();
  }, [limit]);
  if (failed) return <p className="dock-note">Couldn’t reach the team server; the recorded examples below still work.</p>;
  if (!items) return null;
  return (
    <section className="cl-home" aria-labelledby="cl-home-h">
      <div className="lp-section-head">
        <p className="side-label" id="cl-home-h">On the server</p>
        <Link className="text-quiet" href="/campaigns/">All campaigns</Link>
      </div>
      {items.length ? <CampaignRows items={items} compact /> : (
        <p className="lp-plain">No campaigns yet. Start one in a terminal with <code>translucent start</code> (see the README), or let your agent open one over MCP.</p>
      )}
    </section>
  );
}
