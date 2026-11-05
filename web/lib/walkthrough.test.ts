import { describe, expect, test } from "bun:test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";
import {
  afterTestNote, beforeTestNote, blobBytes, codedParam, cRatesLine, idsAsNumbers, labPrediction, parseLoopFile, requestTitle, testsWord, wordsFor,
  type LabTest, type LoopView,
} from "./loop";
import { sentences, walkthroughOf, withoutIds } from "./walkthrough";

const FIXTURES = join(import.meta.dir, "..", "public", "fixtures", "loop");
const read = (name: string) => parseLoopFile(JSON.parse(readFileSync(join(FIXTURES, `${name}.loop.json`), "utf-8")));
const H1 = read("records-to-design");
const v = H1.view as LoopView;
// Raw lookups, independent of the walkthrough's own search: request N is v.requests[N - 1].
const req = (n: number) => v.requests[n - 1].request_id;
const raw = v as unknown as {
  learning: { versions: { version: number; checks?: { coverage: number; heldout_rmse_K: number; heldout_max_error_K: number }[] }[]; lab_tests: LabTest[]; recommended_design: { c_rates: number[]; charge_time_80: number; model_version: number } };
};

describe("the H1 walkthrough, read from its recording", () => {
  const w = walkthroughOf(v)!;

  test("there are five steps, opening at the records and the question", () => {
    expect(w).not.toBeNull();
    expect(w.steps.map((s) => s.n)).toEqual([1, 2, 3, 4, 5]);
    // step 1 shows the records' requests (#1 to #4) in Experiments, not the investigation's missing information
    expect(w.steps[0].focus).toEqual({ tab: "experiments", requestId: req(1), rationale: false });
    expect(w.steps[0].links.map((l) => l.requestId)).toEqual([req(1), req(2), req(3), req(4)]);
    expect(w.planner).toBe("Muse Wonder");
  });

  test("step 2 is the failed check on the other cell, with the recorded numbers", () => {
    const v2 = raw.learning.versions.find((x) => x.version === 2)!.checks![0];
    expect([v2.coverage, v2.heldout_rmse_K, v2.heldout_max_error_K]).toEqual([0.0859, 1.8185, 2.6849]);
    const s = w.steps[1];
    expect(s.focus.requestId).toBe(req(5));
    const facts = s.computed.map((f) => `${f.label}: ${f.value}`).join(" | ");
    expect(facts).toContain("1.82 K");
    expect(facts).toContain("2.68 K");
    expect(facts).toMatch(/8\.6 %|0\.086/);
    expect(s.computed.some((f) => f.tone === "bad")).toBe(true);
    // the planner's sealed prediction reads as "0.95 (0.8 to 1)", not a run of commas
    expect(s.believed).toContain("(0.8 to 1)");
    expect(s.believed).not.toContain("range 0.8");
  });

  test("step 3 is the pooled fit (#6) and step 4 the fixture measurement (#8): 1.5C, then a 60 min rest", () => {
    expect(w.steps[2].focus).toEqual({ tab: "experiments", requestId: req(6), rationale: true });
    expect(w.steps[3].focus).toEqual({ tab: "experiments", requestId: req(8), rationale: true });
    const t3 = raw.learning.lab_tests.find((t) => t.n === 3)!;
    expect(t3.request_id).toBe(req(8));
    expect(t3.protocol).toMatchObject({ c_rate_1: 1.5, c_rate_4: 1.5, rest_min: 60, ambient: 25 });
    expect(w.steps[3].chose).toContain("1.5C");
    expect(w.steps[3].chose).toContain("60 min");
  });

  test("step 5 is the design under v5, with the lab allowance spent and the design itself untested", () => {
    expect(w.steps[4].focus.requestId).toBe(req(12));
    const d = raw.learning.recommended_design;
    const o = w.outcome;
    expect(o.minutes).toBe(d.charge_time_80);
    expect(o.minutes).toBe(35.875);
    expect(d.c_rates).toEqual([1.5, 1.5, 1.2, 0.8]);
    expect(o.rates).toContain("1.5 · 1.5 · 1.2 · 0.8");
    expect(o.modelVersion).toBe(5);
    expect(o.labTests).toEqual({ used: 2, cap: 2, numbers: [3, 4] });
    expect(o.experiments).toEqual({ used: 12, budget: 24 });
    expect(o.designTested).toBe(false);
    expect(o.unresolved.length).toBeGreaterThan(0);
    for (const r of o.unresolved) expect(r).not.toMatch(/\b(rq|res|cmp)_[0-9a-f]{8,}/);
  });

  test("step 5 says the model's numbers came from a check made after the result, and the rule bounds the RMSE", () => {
    const s = w.steps[4].observed;
    expect(s).not.toMatch(/forecast it/);
    expect(s).toContain("Afterwards, model v5, which was not fitted to this test, was checked against it");
    expect(s).toMatch(/RMSE 0\.15 K, inside the 0\.3 K rule/);
    expect(s).not.toMatch(/0\.49 K, within/);
    expect(s).toContain("Muse Wonder had predicted 38 °C");
  });

  test("nothing in the walkthrough carries a request id or says a forecast both was and was not made", () => {
    // the visible text only: requestId fields are ids by design
    const text = w.steps.flatMap((s) => [s.title, s.short, s.believed, s.observed, s.chose, s.quote?.text ?? "", ...s.computed.flatMap((f) => [f.label, f.value]), ...s.links.map((l) => l.label)]).join("\n");
    expect(text).not.toMatch(/\b(rq|res|cmp)_[0-9a-f]{8,}/);
    expect(text).not.toMatch(/forecast[^.]*made before[^.]*no forecast/i);
  });

  test("recordings without this shape get no walkthrough", () => {
    for (const n of ["fastcharge", "fastcharge-lab", "fastcharge-demo"]) expect(walkthroughOf(read(n).view)).toBeNull();
  });
});

describe("rationale sentences", () => {
  test("request ids come out, with their parentheses and punctuation tidied", () => {
    expect(withoutIds("The fit (rq_0123456789abcdef0123) failed.")).toBe("The fit failed.");
    expect(withoutIds("See rq_0123456789abcdef0123: the check , then more")).toBe("See the check, then more");
    expect(withoutIds("A (plain aside) stays.")).toBe("A (plain aside) stays.");
  });
  test("a decimal point does not end a sentence; a new capitalised sentence does", () => {
    expect(sentences("RMSE was 1.82 K. Next I test it.\nNew line here.")).toEqual(["RMSE was 1.82 K.", "Next I test it.", "New line here."]);
    expect(sentences(null)).toEqual([]);
  });
});

describe("what was said about a test before it ran", () => {
  const base = { forecast: null, prediction: null, record: null } as unknown as LabTest;
  test("a model forecast is named only when one is recorded", () => {
    const t = { ...base, forecast: { model_version: 5 } } as unknown as LabTest;
    expect(beforeTestNote(t)).toMatchObject({ kind: "forecast", text: "Forecast under model v5, made before the test ran." });
  });
  test("a sealed prediction without a forecast says so, and gives the prediction", () => {
    const t = { ...base, prediction: { value: 46, lo: 38, hi: 54, unit: "degC" } } as unknown as LabTest;
    const note = beforeTestNote(t, "Muse Wonder");
    expect(note.kind).toBe("prediction");
    expect(note.text).toStartWith("No model forecast of this protocol was recorded; before it ran, Muse Wonder predicted a peak of 46");
    expect(labPrediction(t)).toEqual({ value: 46, lo: 38, hi: 54, unit: "degC" });
  });
  test("a record stretch and a test with nothing recorded say no forecast, once", () => {
    expect(beforeTestNote({ ...base, record: { ref: "x" } } as unknown as LabTest).kind).toBe("none");
    expect(beforeTestNote(base)).toEqual({ kind: "none", text: "No forecast of this protocol was made before the test." });
    expect(labPrediction({ prediction: { value: Number.NaN } } as unknown as LabTest)).toBeNull();
  });
  test("every H1 lab test gets exactly one before-test note, and none claims a forecast (none was recorded)", () => {
    for (const t of raw.learning.lab_tests) {
      expect(t.forecast ?? null).toBeNull();
      expect(beforeTestNote(t).kind).toBe(t.record ? "none" : labPrediction(t) ? "prediction" : "none");
    }
  });
});

describe("coded parameters, ids in prose and labels said from the data", () => {
  test("a bitmask of tests reads as the tests: bit n-1 is test n", () => {
    expect(testsWord(1)).toBe("test 1");
    expect(testsWord(3)).toBe("tests 1 and 2");
    expect(testsWord(4)).toBe("test 3");
    expect(testsWord(8)).toBe("test 4");
    expect(testsWord(13)).toBe("tests 1, 3 and 4");
    for (const bad of [0, -1, 1.5, Number.NaN, "3", null]) expect(testsWord(bad)).toBeNull();
  });

  test("every fit and check in every recording names its tests, never the raw mask", () => {
    for (const name of ["records-to-design", "fastcharge-lab", "fastcharge", "fastcharge-demo"]) {
      const view = read(name).view;
      for (const r of view.requests) {
        const line = requestTitle(r);
        expect(line).not.toMatch(/\b(Fit on|Test on) \d/);
        if ("fit_on" in r.params) expect(line).toMatch(/Fitted on tests? \d/);
        if ("test_on" in r.params) expect(line).toMatch(/Checked on tests? \d/);
        if ("mode" in r.params && "segment" in r.params) expect(line).toMatch(/Segment read as a (dis)?charge/);
      }
    }
    // H1, against its lab tests: #6 pooled both records, #9 fitted test 3, #11 checked test 4
    expect(requestTitle(v.requests[5])).toContain("Fitted on tests 1 and 2");
    expect(requestTitle(v.requests[8])).toContain("Fitted on test 3");
    expect(requestTitle(v.requests[10])).toContain("Checked on test 4");
    expect(requestTitle(v.requests[8])).toContain("Model family cooling and thermal mass");
    // a "mode" or "family" outside these actions is left as recorded
    expect(codedParam("mode", { value: 1, unit: "" }, { mode: { value: 1, unit: "" } })).toBeNull();
  });

  test("request ids in prose read as request numbers; result and comparison ids go", () => {
    const t = "Rests on rq_c7c2d9d06f43ad1fe752 (assessment / rq_c7c2d9d06f43ad1fe752 comparison cmp_c16c98525310040aece76208: verdict inconclusive). Fit (rq_7380df95c3e00baa135a: h=5.744) and res_0c41d57be7228e72831d31fc.";
    const out = idsAsNumbers(t, v.requests);
    expect(out).toBe("Rests on #12 (assessment / #12 comparison: verdict inconclusive). Fit (#9: h=5.744) and.");
    expect(idsAsNumbers(v.conclusion!.text, v.requests)).not.toMatch(/\b(rq|res|cmp)_[0-9a-f]{8,}/);
    for (const r of v.conclusion!.unresolved_reasons) expect(idsAsNumbers(r, v.requests)).not.toMatch(/\b(rq|res|cmp)_[0-9a-f]{8,}/);
  });

  test("a constant rate reads as one rate; the target's name drops its forecast note", () => {
    expect(cRatesLine([1, 1, 1, 1])).toBe("1C throughout");
    expect(cRatesLine([1.5, 1.5, 1.2, 0.8])).toBe("1.5 · 1.5 · 1.2 · 0.8C");
    const words = wordsFor(v);
    expect(v.objective.target.label).toContain("(a forecast: the slowest member)");
    expect(words.quantity("charge_time_80")).toBe("Time from 10 to 80 % SOC");
    expect(words.note("charge_time_80")).toBe("a forecast: the slowest member");
  });

  test("after a test: the held-out check that replayed it, said as computed afterwards", () => {
    const t4 = raw.learning.lab_tests.find((t) => t.n === 4)!;
    const after = afterTestNote(v.learning!, t4, v.requests)!;
    expect(after).toMatchObject({ version: 5, requestNumber: 11, rmse: 0.1533, maxError: 0.49 });
    expect(after.text).toBe("After it ran, a held-out check (#11) set model v5, which was not fitted to it, against the result: RMSE 0.15 K, worst error 0.49 K.");
    // test 3 was fitted, never checked; the records' test 2 was checked against v2
    expect(afterTestNote(v.learning!, raw.learning.lab_tests.find((t) => t.n === 3)!, v.requests)).toBeNull();
    expect(afterTestNote(v.learning!, raw.learning.lab_tests.find((t) => t.n === 2)!, v.requests)?.version).toBe(2);
  });
});

describe("uploaded files carried in a recording", () => {
  test("a gzip blob decodes to the bytes whose hash it is filed under", async () => {
    const body = new TextEncoder().encode("t,V\n0,3.1\n1,3.2\n");
    const sha = createHash("sha256").update(body).digest("hex");
    const blobs = { [`sha256:${sha}`]: { sha256: sha, bytes: body.length, encoding: "gzip+base64", data: gzipSync(body).toString("base64") } };
    expect(await blobBytes(blobs as never, sha)).toEqual(body);
    expect(await blobBytes(blobs as never, "sha256:missing")).toBeNull();
  });
  test("the H1 uploads are carried whole: each decodes to bytes matching its recorded SHA-256", async () => {
    const uploads = v.investigation?.uploads ?? [];
    expect(uploads.map((u) => u.name)).toEqual(["LGM50_cell02.csv", "LGM50_cell03.csv"]);
    for (const u of uploads) {
      const bytes = await blobBytes(H1.blobs ?? {}, u.sha256!);
      expect(bytes).not.toBeNull();
      expect(`sha256:${createHash("sha256").update(bytes!).digest("hex")}`.replace(/^sha256:sha256:/, "sha256:")).toBe(u.sha256!.startsWith("sha256:") ? u.sha256! : `sha256:${u.sha256}`);
    }
  });
});
