// Small line icons drawn for this app on a 20-unit grid. Stroke is currentColor; size follows the font unless set.
import type { ReactNode, SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 18, children, ...rest }: P & { children: ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.5}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden focusable="false" {...rest}>
      {children}
    </svg>
  );
}

export const IconNew = (p: P) => <Svg {...p}><path d="M9 3.5H5.5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V11" /><path d="m14.2 3.3 2.5 2.5-6.9 6.9-3.1.6.6-3.1z" /></Svg>;
export const IconChats = (p: P) => <Svg {...p}><path d="M3.5 5.5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v4.5a2 2 0 0 1-2 2H8l-3 2.5v-2.5h0a2 2 0 0 1-1.5-2z" /><path d="M14.5 7.5h.5a2 2 0 0 1 2 2V14l-1.8-1.5" /></Svg>;
export const IconFlask = (p: P) => <Svg {...p}><path d="M8 3h4M8.5 3v4.6L4.2 15a1.4 1.4 0 0 0 1.2 2h9.2a1.4 1.4 0 0 0 1.2-2l-4.3-7.4V3" /><path d="M6.2 12.5h7.6" /></Svg>;
export const IconMemory = (p: P) => <Svg {...p}><path d="M5.5 3.5h9v13l-4.5-3-4.5 3z" /></Svg>;
export const IconProof = (p: P) => <Svg {...p}><path d="M10 2.8 4 5.2v4.6c0 3.4 2.6 6.1 6 7.4 3.4-1.3 6-4 6-7.4V5.2z" /><path d="m7.4 10 1.9 1.9 3.4-3.6" /></Svg>;
export const IconSun = (p: P) => <Svg {...p}><circle cx="10" cy="10" r="3.2" /><path d="M10 2.5v1.6M10 15.9v1.6M2.5 10h1.6M15.9 10h1.6M4.7 4.7l1.1 1.1M14.2 14.2l1.1 1.1M4.7 15.3l1.1-1.1M14.2 5.8l1.1-1.1" /></Svg>;
export const IconMoon = (p: P) => <Svg {...p}><path d="M16 12.2A6.5 6.5 0 0 1 7.8 4a6.5 6.5 0 1 0 8.2 8.2z" /></Svg>;
export const IconSidebar = (p: P) => <Svg {...p}><rect x="3" y="4" width="14" height="12" rx="2.5" /><path d="M8 4v12" /></Svg>;
export const IconMenu = (p: P) => <Svg {...p}><path d="M3.5 6h13M3.5 10h13M3.5 14h9" /></Svg>;
export const IconClose = (p: P) => <Svg {...p}><path d="m5 5 10 10M15 5 5 15" /></Svg>;
export const IconPlus = (p: P) => <Svg {...p}><path d="M10 4v12M4 10h12" /></Svg>;
export const IconUp = (p: P) => <Svg {...p}><path d="M10 15.5v-11M5.5 9 10 4.5 14.5 9" /></Svg>;
export const IconChevron = (p: P) => <Svg {...p}><path d="m7.5 5 5 5-5 5" /></Svg>;
export const IconDown = (p: P) => <Svg {...p}><path d="m5.5 8 4.5 4.5L14.5 8" /></Svg>;
export const IconEye = (p: P) => <Svg {...p}><path d="M2.5 10s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5z" /><circle cx="10" cy="10" r="2.2" /></Svg>;
export const IconBook = (p: P) => <Svg {...p}><path d="M4 4.5A1.5 1.5 0 0 1 5.5 3H16v12H5.5A1.5 1.5 0 0 0 4 16.5z" /><path d="M4 16.5A1.5 1.5 0 0 0 5.5 18H16v-3" /></Svg>;
export const IconWave = (p: P) => <Svg {...p}><path d="M2.5 11h3l2-5 3 9 2.2-6 1.3 2h3.5" /></Svg>;
export const IconShape = (p: P) => <Svg {...p}><path d="M6 4.5c3-1.5 8 .5 9.5 4s-1 7.5-5 7.5S3 14 3.5 10 4 5.5 6 4.5z" strokeDasharray="2.4 2.2" /></Svg>;
export const IconRuler = (p: P) => <Svg {...p}><rect x="2.5" y="7" width="15" height="6" rx="1.2" /><path d="M6 7v2.5M9 7v1.5M12 7v2.5M15 7v1.5" /></Svg>;
export const IconCheck = (p: P) => <Svg {...p}><path d="m4.5 10.5 3.5 3.5 7.5-8" /></Svg>;
export const IconPlay = (p: P) => <Svg {...p}><path d="M6.5 4.5v11l9-5.5z" fill="currentColor" stroke="none" /></Svg>;
export const IconPause = (p: P) => <Svg {...p}><path d="M7 4.5v11M13 4.5v11" strokeWidth={2} /></Svg>;
export const IconStop = (p: P) => <Svg {...p}><rect x="5.5" y="5.5" width="9" height="9" rx="2" fill="currentColor" stroke="none" /></Svg>;
export const IconRestart = (p: P) => <Svg {...p}><path d="M4.2 10a5.8 5.8 0 1 0 1.8-4.2M4 3v3.6h3.6" /></Svg>;
export const IconFilm = (p: P) => <Svg {...p}><rect x="3" y="4" width="14" height="12" rx="2.5" /><path d="m8.5 7.5 4 2.5-4 2.5z" fill="currentColor" stroke="none" /></Svg>;
export const IconUpload = (p: P) => <Svg {...p}><path d="M10 13V3.5M6 7.5l4-4 4 4M3.5 13v2a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-2" /></Svg>;
export const IconBolt = (p: P) => <Svg {...p}><path d="M11 2.5 4.5 11H10l-1 6.5L15.5 9H10z" /></Svg>;
export const IconRoute = (p: P) => <Svg {...p}><circle cx="5" cy="14.5" r="1.8" /><circle cx="15" cy="5.5" r="1.8" /><path d="M6.8 14.5H12a2.5 2.5 0 0 0 0-5H8a2.5 2.5 0 0 1 0-5h5.2" /></Svg>;
export const IconAim = (p: P) => <Svg {...p}><circle cx="10" cy="10" r="5.5" /><path d="M10 2.5v3M10 14.5v3M2.5 10h3M14.5 10h3" /><circle cx="10" cy="10" r="0.9" fill="currentColor" stroke="none" /></Svg>;
export const IconNote =(p: P) => <Svg {...p}><path d="M4 4.5h12v8.5H9l-3.5 3v-3H4z" /></Svg>;

/** A quiet spinner: one arc turning. */
export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg className="spinner" width={size} height={size} viewBox="0 0 20 20" fill="none" aria-hidden>
      <circle cx="10" cy="10" r="7.5" stroke="currentColor" strokeOpacity="0.2" strokeWidth="2" />
      <path d="M17.5 10A7.5 7.5 0 0 0 10 2.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
