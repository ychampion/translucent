import { expect, test } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Composer } from "./Composer";

// The live page puts its own form (Join: name and team key) in the tray. HTML ignores a form nested in another
// form, so Join would submit the composer's form instead and save nothing.
test("the tray is outside the composer's form, so a form in it submits itself", () => {
  const tray = createElement("form", { className: "tray-join" }, createElement("button", null, "Join"));
  const html = renderToStaticMarkup(createElement(Composer, {
    value: "", onChange() {}, onSubmit() {}, placeholder: "Tell the agent something", canSend: false,
    label: "Tell the agent something", tray,
  }));
  const at = html.indexOf('<form class="tray-join"');
  expect(at).toBeGreaterThan(-1);
  const before = html.slice(0, at);
  expect(before.split("<form").length - before.split("</form>").length).toBe(0);
  expect(html.split("<form").length - 1).toBe(2);  // the tray's form and the composer's own
});
