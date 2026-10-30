// The reader's reduced-motion preference, read once at first paint (layout.tsx sets data-reduced-motion on <html>).

export const REDUCED_MOTION_SCRIPT =
  `try{if(matchMedia("(prefers-reduced-motion: reduce)").matches)document.documentElement.dataset.reducedMotion="1";}catch(e){}`;

export function reducedMotion(): boolean {
  return typeof document !== "undefined" && document.documentElement.dataset.reducedMotion === "1";
}

/** "smooth", unless the reader asked for less motion. */
export const scrollBehavior = (): ScrollBehavior => (reducedMotion() ? "auto" : "smooth");
