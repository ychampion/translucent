"use client";
// The app frame: a slim icon rail, a collapsible sidebar (new campaign, experiments, recent campaigns) and the page.
// On a phone the rail and sidebar fold into a drawer opened from the page header.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { EXPERIMENTS } from "@/lib/campaigns";
import { useRecents, useRunning } from "@/lib/recents";
import { IconChats, IconClose, IconFlask, IconMemory, IconMoon, IconNew, IconProof, IconSidebar, IconSun } from "./Icons";

type Theme = "dark" | "light";
const THEME_KEY = "translucent.theme";
const SIDE_KEY = "translucent.sidebar";

interface ShellApi { openDrawer(): void; toggleSidebar(): void; sidebar: boolean }
const ShellContext = createContext<ShellApi>({ openDrawer() {}, toggleSidebar() {}, sidebar: true });
export const useShell = () => useContext(ShellContext);

/** Set before first paint (layout.tsx) so the page never flashes the other theme. */
export const THEME_SCRIPT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;}catch(e){}`;

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

  return (
    <ShellContext.Provider value={{ openDrawer: () => setDrawer(true), toggleSidebar, sidebar }}>
      <div className={`app ${sidebar ? "" : "no-sidebar"} ${drawer ? "drawer-open" : ""}`}>
        <nav className="rail" aria-label="Main">
          <RailLink href="/" label="New campaign" on={path === "/"}><IconNew /></RailLink>
          <button className="rail-btn" onClick={toggleSidebar} aria-label={sidebar ? "Hide sidebar" : "Show sidebar"} title="Campaigns">
            <IconChats />
          </button>
          <button className="rail-btn" onClick={() => { if (!sidebar) toggleSidebar(); document.getElementById("side-experiments")?.focus(); }} aria-label="Experiments" title="Experiments">
            <IconFlask />
          </button>
          <RailLink href={`/c/${memoryKey}/memory/`} label="Memory" on={path.endsWith("/memory")}><IconMemory /></RailLink>
          <RailLink href="/proof/" label="How it’s checked" on={path.startsWith("/proof")}><IconProof /></RailLink>
          <span className="rail-gap" />
          <button className="rail-btn" onClick={toggleTheme} aria-label={theme === "dark" ? "Light theme" : "Dark theme"} title={theme === "dark" ? "Light theme" : "Dark theme"}>
            {theme === "dark" ? <IconSun /> : <IconMoon />}
          </button>
        </nav>

        <aside className="sidebar" aria-label="Campaigns">
          <Sidebar path={path} onClose={() => setDrawer(false)} theme={theme} toggleTheme={toggleTheme} />
        </aside>
        <div className="scrim" onClick={() => setDrawer(false)} aria-hidden />

        <div className="main">{children}</div>
      </div>
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

/** Short names for the experiments list. */
const SHORT: Record<string, string> = { m4: "Movie 4", lu: "Graphite" };

function Sidebar({ path, onClose, theme, toggleTheme }: { path: string; onClose(): void; theme: Theme; toggleTheme(): void }) {
  const recents = useRecents();
  const running = useRunning();
  const [search, setSearch] = useState("");
  useEffect(() => { setSearch(window.location.search); }, [path]);
  const here = (href: string) => {
    const [p, q] = href.split("?");
    if (p.replace(/(.)\/$/, "$1") !== path) return false;
    const live = new URLSearchParams(q ?? "").get("live");
    return (new URLSearchParams(search).get("live") ?? null) === (live ?? null);
  };
  return (
    <div className="side">
      <div className="side-head">
        <Link href="/" className="wordmark" aria-label="Translucent, new campaign">
          <svg width="18" height="18" viewBox="0 0 20 20" aria-hidden>
            <circle cx="8" cy="10" r="6.5" className="wm-a" />
            <circle cx="12" cy="10" r="6.5" className="wm-b" />
          </svg>
          Translucent
        </Link>
        <button className="icon-btn side-close" onClick={onClose} aria-label="Close menu"><IconClose /></button>
      </div>
      <Link href="/" className={`side-row is-new ${path === "/" ? "is-on" : ""}`}><IconNew size={16} />New campaign</Link>

      <p className="side-label" id="side-experiments" tabIndex={-1}>Experiments</p>
      {EXPERIMENTS.map((x) => (
        <Link key={x.key} href={`/?x=${x.key}`} className="side-row">{SHORT[x.key] ?? x.name}</Link>
      ))}
      <Link href="/?x=own" className="side-row">Your recordings</Link>

      <p className="side-label">Recent</p>
      <div className="side-list">
        {recents.map((r) => (
          <Link key={r.href} href={r.href} className={`side-row ${here(r.href) ? "is-on" : ""}`} title={r.title}>
            <span className="side-title">{r.title}</span>
            {running === r.href ? <span className="live-dot" aria-label="Running" /> : r.live && <span className="side-tag">Live</span>}
          </Link>
        ))}
      </div>

      <div className="side-foot">
        <Link href="/proof/" className="side-row">How it’s checked</Link>
        <button className="side-row" onClick={toggleTheme}>{theme === "dark" ? <IconSun size={16} /> : <IconMoon size={16} />}{theme === "dark" ? "Light theme" : "Dark theme"}</button>
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
      <div className="page-actions">{children}</div>
    </header>
  );
}
