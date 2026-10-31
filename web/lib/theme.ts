// The saved colour theme. Kept out of the "use client" Shell so the server layout gets the script's text: a constant
// exported from a client module reaches a server component as a client reference, not as the string.

export const THEME_KEY = "translucent.theme";

/** Set before first paint (layout.tsx) so the page never flashes the other theme. */
export const THEME_SCRIPT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;}catch(e){}`;
