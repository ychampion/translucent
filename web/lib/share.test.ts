import { describe, expect, test } from "bun:test";
import { absoluteUrl } from "./share";

describe("share links keep the base path", () => {
  const path = "/c/m4/finding/?id=c1";
  test("served at the root", () => {
    expect(absoluteUrl("https://x.com", "", path)).toBe("https://x.com/c/m4/finding/?id=c1");
  });
  test("served under /demo", () => {
    expect(absoluteUrl("https://translucent.axcelner.com", "/demo", path)).toBe("https://translucent.axcelner.com/demo/c/m4/finding/?id=c1");
  });
  test("a site address that already names the base path does not get it twice", () => {
    expect(absoluteUrl("https://translucent.axcelner.com/demo/", "/demo", path)).toBe("https://translucent.axcelner.com/demo/c/m4/finding/?id=c1");
  });
});
