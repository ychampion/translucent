"use client";
// The app frame: a slim icon rail (show/hide the sidebar, theme), a collapsible sidebar (new campaign, the server's
// campaigns, recent pages, the recorded engineering example and a link to the other recorded examples) and the page.
// Each destination is listed once. On a phone the sidebar folds into a drawer opened from the page header.
// The session: asked once on load; the sign-in dialog opens over any page whose call answered 401.
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { ApiError, v1 } from "@/lib/api";
import { useSession } from "@/lib/session";
import { INTERVENTION_NAV, interventionHref } from "@/lib/intervention";
import { dispatchKey, useCommands } from "@/lib/commands";
import { FASTCHARGE_DEMO_HREF, FASTCHARGE_HREF, FASTCHARGE_LAB_HREF, loopHref, type CampaignSummary } from "@/lib/loop";
import { useAttention, useRecents, useRunning, useUnread } from "@/lib/recents";
import { apiConfigured } from "@/lib/sources";
import { THEME_KEY } from "@/lib/theme";
import { IconAim, IconChats, IconClose, IconMoon, IconNew, IconSidebar, IconSun } from "./Icons";
import { LogoMark } from "./Logo";
import { Palette, ShortcutSheet } from "./Palette";
import { Account, SignInDialog, useSessionProbe } from "./Session";

type Theme = "dark" | "light";
const SIDE_KEY = "translucent.sidebar";

interface ShellApi { openDrawer(): void; toggleSidebar(): void; sidebar: boolean }
const ShellContext = createContext<ShellApi>({ openDrawer() {}, toggleSidebar() {}, sidebar: true });
export const useShell = () => useContext(ShellContext);

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>("dark");
  useEffect(() => { setTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark"); }, []);
  const toggle = useCallback(() => {
    setTheme((t) => {
      const next = t === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem(THEME_KEY, next); } catch { /* this tab only */ }
      return next;
    });
  }, []);
  return [theme, toggle];
}

/** The campaign key in the current path ("/c/lu/finding/" -> "lu"). */
const keyIn = (path: string) => path.match(/^\/c\/([^/]+)/)?.[1] ?? null;

export function Shell({ children }: { children: ReactNode }) {
  useSessionProbe();
  const path = (usePathname() ?? "/").replace(/(.)\/$/, "$1");
  const [theme, toggleTheme] = useTheme();
  const [sidebar, setSidebar] = useState(true);
  const [drawer, setDrawer] = useState(false);
  useEffect(() => {
    try { if (localStorage.getItem(SIDE_KEY) === "0") setSidebar(false); } catch { /* default open */ }
  }, []);
  useEffect(() => { setDrawer(false); }, [path]);
  const toggleSidebar = useCallback(() => {
    setSidebar((s) => {
      try { localStorage.setItem(SIDE_KEY, s ? "0" : "1"); } catch { /* this tab only */ }
      return !s;
    });
  }, []);
  const key = keyIn(path);
  const memoryKey = key && key !== "own" ? key : "lu";
  const router = useRouter();
  const recents = useRecents();
  const [palette, setPalette] = useState(false);
  const [keysSheet, setKeysSheet] = useState(false);

  // Every shortcut goes through the command registry.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { dispatchKey(e); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useCommands("shell", () => [
    { id: "palette", label: "Search findings and commands", group: "Help", keys: ["Ctrl+K"], inFields: true, quiet: true, run: () => { setKeysSheet(false); setPalette((p) => !p); } },
    { id: "keys", label: "Keyboard shortcuts", group: "Help", keys: ["?", "Ctrl+/"], run: () => { setPalette(false); setKeysSheet((k) => !k); } },
    { id: "home", label: "Home", group: "Navigate", run: () => router.push("/") },
    { id: "new", label: "New campaign", group: "Navigate", run: () => router.push("/new/") },
    { id: "examples", label: "Other recorded examples", group: "Navigate", run: () => router.push("/examples/") },
    { id: "campaigns", label: "Campaigns on the server", group: "Navigate", run: () => router.push("/campaigns/") },
    { id: "memory", label: "Memory", group: "Navigate", run: () => router.push(`/c/${memoryKey}/memory/`) },
    { id: "proof", label: "How it’s checked", group: "Navigate", run: () => router.push("/proof/") },
    { id: "compare", label: "Compare two runs of Movie 4", hint: "With the team vs the earlier run with typed-in numbers", group: "Compare", run: () => router.push("/compare/?a=m4-team&b=m4-meta") },
    ...INTERVENTION_NAV.map((x) => ({ id: `intervention:${x.key}`, label: x.name, hint: "Stand down, escalate or watch: scored on recorded trials", group: "Navigate" as const, run: () => router.push(interventionHref(x.key)) })),
    { id: "theme", label: theme === "dark" ? "Light theme" : "Dark theme", group: "Navigate", run: toggleTheme },
    { id: "sidebar", label: sidebar ? "Hide the sidebar" : "Show the sidebar", group: "Navigate", run: toggleSidebar },
    ...recents.slice(0, 8).map((r) => ({ id: `recent:${r.href}`, label: r.title, hint: "Recent campaign", group: "Navigate" as const, run: () => router.push(r.href) })),
  ], [theme, sidebar, recents, memoryKey]);

  return (
    <ShellContext.Provider value={{ openDrawer: () => setDrawer(true), toggleSidebar, sidebar }}>
      <a className="skip-link" href="#main">Skip to content</a>
      <div className={`app ${sidebar ? "" : "no-sidebar"} ${drawer ? "drawer-open" : ""}`}>
        <nav className="rail" aria-label="Main">
          <button className="rail-btn" onClick={toggleSidebar} aria-label={sidebar ? "Hide sidebar" : "Show sidebar"} title={sidebar ? "Hide sidebar" : "Show sidebar"}>
            <IconChats />
          </button>
          {!sidebar && <RailLink href="/new/" label="New campaign" on={path === "/new"}><IconNew /></RailLink>}
          <span className="rail-gap" />
          <button className="rail-btn" onClick={toggleTheme} aria-label={theme === "dark" ? "Light theme" : "Dark theme"} title={theme === "dark" ? "Light theme" : "Dark theme"}>
            {theme === "dark" ? <IconSun /> : <IconMoon />}
          </button>
        </nav>

        <aside className="sidebar" aria-label="Campaigns">
          <Sidebar path={path} onClose={() => setDrawer(false)} theme={theme} toggleTheme={toggleTheme} />
        </aside>
        <div className="scrim" onClick={() => setDrawer(false)} aria-hidden />

        <div className="main" id="main" tabIndex={-1}>{children}</div>
      </div>
      <SignInDialog />
      {palette && <Palette onClose={() => setPalette(false)} />}
      {keysSheet && <ShortcutSheet onClose={() => setKeysSheet(false)} />}
    </ShellContext.Provider>
  );
}

function RailLink({ href, label, on, children }: { href: string; label: string; on: boolean; children: ReactNode }) {
  return (
    <Link href={href} className={`rail-btn ${on ? "is-on" : ""}`} aria-label={label} title={label} aria-current={on ? "page" : undefined}>
      {children}
    </Link>
  );
}


/**
 * The newest campaigns of every ledger on the team server, for the sidebar. Null while loading or without a server;
 * "error" when the server didn't answer. Re-read when the page changes, so a new campaign shows up.
 */
function useServerCampaigns(path: string, limit = 8): CampaignSummary[] | null | "error" | "signin" {
  const { epoch } = useSession();
  const [items, setItems] = useState<CampaignSummary[] | null | "error" | "signin">(null);
  useEffect(() => {
    if (!apiConfigured()) return;
    const ctl = new AbortController();
    (async () => {
      const ledgers = await v1.ledgers(ctl.signal);
      const pages = await Promise.all(ledgers.map((l) => v1.campaigns(l.id, null, ctl.signal)));
      setItems(pages.flatMap((p) => p.items).sort((a, b) => b.updated_at - a.updated_at).slice(0, limit));
    })().catch((e) => { if (!ctl.signal.aborted) setItems((x) => (Array.isArray(x) ? x : e instanceof ApiError && e.status === 401 ? "signin" : "error")); });
    return () => ctl.abort();
  }, [path, limit, epoch]);
  return items;
}

/** The dot beside a campaign: gold when it waits for a person, blue while something runs; none once it rests. */
function campaignDot(c: CampaignSummary): { tone: string; label: string } | null {
  if (c.next_action === "awaiting_approval" || c.status === "waiting_for_approval") return { tone: "is-wait", label: "Waiting for your approval" };
  if (c.next_action === "awaiting_result" || c.status === "waiting_for_result") return { tone: "is-wait", label: "Waiting for a person’s result" };
  if (c.next_action === "planner_stopped") return { tone: "is-wait", label: "The planner stopped: decide whether to raise its budget or conclude" };
  if (c.next_action === "running" || c.next_action === "planner_thinking" || c.status === "running") return { tone: "is-busy", label: "Running" };
  return null;
}

function Sidebar({ path, onClose, theme, toggleTheme }: { path: string; onClose(): void; theme: Theme; toggleTheme(): void }) {
  const recents = useRecents();
  const running = useRunning();
  const attention = useAttention();
  const unread = useUnread();
  const server = useServerCampaigns(path);
  const [search, setSearch] = useState("");
  useEffect(() => { setSearch(window.location.search); }, [path]);
  const here = (href: string) => {
    const [p, q] = href.split("?");
    if (p.replace(/(.)\/$/, "$1") !== path) return false;
    const want = new URLSearchParams(q ?? ""), have = new URLSearchParams(search);
    if (want.get("id")) return want.get("id") === have.get("id") && want.get("ledger") === have.get("ledger");
    return (have.get("live") ?? null) === (want.get("live") ?? null);
  };
  const serverHrefs = new Set(Array.isArray(server) ? server.filter((c) => c.kind === "loop").map((c) => loopHref(c.ledger, c.id)) : []);
  const recentOnly = recents.filter((r) => !serverHrefs.has(r.href));
  return (
    <div className="side">
      <div className="side-head">
        <Link href="/" className="wordmark" aria-label="Translucent, home">
          <LogoMark size={20} />
          Translucent
        </Link>
        <button className="icon-btn side-close" onClick={onClose} aria-label="Close menu"><IconClose /></button>
      </div>
      <Link href="/new/" className={`side-row is-new ${path === "/new" ? "is-on" : ""}`}><IconNew size={16} />New campaign</Link>

      <div className="side-scroll">
        <div className="side-label-row">
          <p className="side-label">Campaigns</p>
          <Link href="/campaigns/" className={`side-more ${path === "/campaigns" ? "is-on" : ""}`}>All</Link>
        </div>
        {Array.isArray(server) && server.length > 0 ? (
          <div className="side-list">
            {server.map((c) => {
              const dot = campaignDot(c);
              const body = (
                <>
                  <span className="side-title">{c.title}</span>
                  {dot && <span className={`side-dot ${dot.tone}`} aria-label={dot.label} title={dot.label} />}
                </>
              );
              return c.kind === "loop"
                ? <Link key={`${c.ledger}/${c.id}`} href={loopHref(c.ledger, c.id)} className={`side-row is-campaign ${here(loopHref(c.ledger, c.id)) ? "is-on" : ""}`} title={c.title}>{body}</Link>
                : <Link key={`${c.ledger}/${c.id}`} href={`/campaigns/?ledger=${encodeURIComponent(c.ledger)}`} className="side-row is-campaign" title={c.title}>{body}</Link>;
            })}
          </div>
        ) : (
          <>
            <Link href="/campaigns/" className={`side-row ${path === "/campaigns" ? "is-on" : ""}`}><IconAim size={16} />Campaigns on the server</Link>
            {server === "error" && <p className="side-note">The team server didn’t answer.</p>}
            {server === "signin" && <p className="side-note">Sign in to see them.</p>}
            {Array.isArray(server) && <p className="side-note">No campaigns yet. Start one with New campaign.</p>}
          </>
        )}

        {recentOnly.length > 0 && <p className="side-label">Recent</p>}
        <div className="side-list">
          {recentOnly.map((r) => (
            <Link key={r.href} href={r.href} className={`side-row ${here(r.href) ? "is-on" : ""}`} title={r.title}>
              <span className="side-title">{r.title}</span>
              {(attention[r.href] ?? 0) > 0 && (
                <span className="side-badge num" aria-label={`${attention[r.href]} waiting for you`}>{attention[r.href]}</span>
              )}
              {unread.includes(r.href) && <span className="unread-dot" aria-label="Finished while you were away" />}
              {running === r.href ? <span className="live-dot" aria-label="Running" /> : r.live && <span className="side-tag">Live</span>}
            </Link>
          ))}
        </div>

        <p className="side-label">Recorded</p>
        <Link href={FASTCHARGE_LAB_HREF} className={`side-row ${path === "/w" && new URLSearchParams(search).get("recording") === "fastcharge-lab" ? "is-on" : ""}`}>
          <span className="side-title">Learning the lab&rsquo;s thermal model</span>
        </Link>
        <Link href={FASTCHARGE_HREF} className={`side-row ${path === "/w" && new URLSearchParams(search).get("recording") === "fastcharge" ? "is-on" : ""}`}>
          <span className="side-title">Faster LG M50 charge</span>
        </Link>
        <Link href={FASTCHARGE_DEMO_HREF} className={`side-row ${path === "/w" && new URLSearchParams(search).get("recording") === "fastcharge-demo" ? "is-on" : ""}`}>
          <span className="side-title">Case B, browser demonstration</span>
        </Link>
        <Link href="/examples/" className={`side-row ${path === "/examples" || path.startsWith("/i/") || path.startsWith("/c/") ? "is-on" : ""}`}>Other recorded examples</Link>
      </div>

      <div className="side-foot">
        <Link href="/proof/" className="side-row">How it’s checked</Link>
        <div className="side-account only-phone-flex"><Account /></div>
        <button className="side-row only-phone-flex" onClick={toggleTheme}>{theme === "dark" ? <IconSun size={16} /> : <IconMoon size={16} />}{theme === "dark" ? "Light theme" : "Dark theme"}</button>
      </div>
    </div>
  );
}

/** A page's slim header: menu (phone), sidebar toggle (when hidden), a title and actions on the right. */
export function PageHeader({ title, children }: { title?: ReactNode; children?: ReactNode }) {
  const shell = useShell();
  return (
    <header className="page-head">
      <button className="icon-btn only-phone" onClick={shell.openDrawer} aria-label="Open menu"><IconSidebar /></button>
      {!shell.sidebar && <button className="icon-btn only-wide" onClick={shell.toggleSidebar} aria-label="Show sidebar"><IconSidebar /></button>}
      {title != null && <h1 className="page-title">{title}</h1>}
      <div className="page-actions">{children}<Account /></div>
    </header>
  );
}
