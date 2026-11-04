import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  decimalOf, expected, headerColumns, initialForm, missing, newUploadId, readHead, refusedReasons, resultBody,
  type ApprovedRequest, type ResultSpec,
} from "./result";

// the lab system's bench test, as GET .../systems lists it (the fields the form reads)
const SPEC: ResultSpec = {
  id: "pybamm-fastcharge-lab",
  quantities: [
    { name: "status", unit: "", kind: "category", categories: ["ok", "stopped", "failed"] },
    { name: "max_temperature", unit: "degC", kind: "scalar", label: "Peak surface temperature" },
    { name: "temperature_end", unit: "degC", kind: "scalar" },
    { name: "surface_temperature", unit: "degC", kind: "series", label: "Surface temperature" },
    { name: "voltage", unit: "V", kind: "series" },
    { name: "current", unit: "A", kind: "series" },
  ],
  actions: [{ name: "bench_charge", executor: "human", parameters: [{ name: "c_rate_1", unit: "1/h" }, { name: "ambient", unit: "degC" }],
    produces: ["status", "max_temperature", "temperature_end", "surface_temperature", "voltage", "current"] }],
};
const REQ: ApprovedRequest = {
  request_id: "rq_1", version: 2, state: "awaiting_result", action: "bench_charge", executor: "human", content_hash: "ab".repeat(32),
  params: { c_rate_1: { value: 1.5, unit: "1/h" }, ambient: { value: 25, unit: "degC" } }, procedure: "Charge at 1.5C", specimen: { id: "lgm50-bench-01" },
};
// exact lines of a logger export a planner imported (German settings: semicolons, decimal commas, a preamble)
const EXPORT = readFileSync(join(import.meta.dir, "..", "..", "tests", "data", "import_v2", "cell02_export_excerpt.csv"), "utf8");

describe("returning a person's result", () => {
  test("the form asks for every measurement the action produces: series as columns, the rest as values", () => {
    const e = expected(SPEC, "bench_charge");
    expect(e.series.map((q) => q.name)).toEqual(["surface_temperature", "voltage", "current"]);
    expect(e.values.map((q) => q.name)).toEqual(["status", "max_temperature", "temperature_end"]);
  });

  test("a logger export's header, delimiter and decimal comma are read from its first lines", () => {
    const h = readHead(EXPORT);
    expect(h.delimiter).toBe(";");
    expect(h.headerRow).toBe(5);         // five preamble lines (one blank) before the header
    expect(headerColumns(h.lines, h.headerRow, h.delimiter)).toEqual(["elapsed [s]", "step", "mode", "I", "U", "Q", "T1", "T2"]);
    expect(decimalOf(h.lines, h.headerRow, h.delimiter)).toBe(",");
    // the first data row is not skipped: no units line follows this header
    expect(h.lines[h.headerRow + 1].startsWith("0;1;R;")).toBe(true);
  });

  test("the body names the approved version, the configuration as run in its units, and the table in the import grammar", () => {
    const f = initialForm(REQ, SPEC);
    expect(f.specimen).toBe("lgm50-bench-01");
    expect(f.executed).toEqual({ c_rate_1: "1.5", ambient: "25" });
    expect(missing(f, REQ, SPEC)).toContain("choose the logged file");
    const h = readHead(EXPORT);
    Object.assign(f, { file: "cell02_export_excerpt.csv", headerRow: h.headerRow, delimiter: h.delimiter, decimal: ",", timeColumn: "elapsed [s]",
      columns: { surface_temperature: { column: "T2", unit: "K" }, voltage: { column: "U", unit: "V" }, current: { column: "I", unit: "mA" } },
      values: { status: { value: "ok", unit: "", source: "the operator" }, max_temperature: { value: "38,4", unit: "degC", source: "logger peak" },
        temperature_end: { value: "26.1", unit: "degC", source: "logger" } } });
    expect(missing(f, REQ, SPEC)).toEqual([]);
    const b = resultBody(f, REQ, SPEC) as Record<string, any>;
    expect(b.content_hash).toBe(REQ.content_hash);
    expect(b.executed_configuration).toEqual({ c_rate_1: 1.5, ambient: 25 });
    expect(b.executed_units).toEqual({ c_rate_1: "1/h", ambient: "degC" });
    expect(b.table).toEqual({ file: "cell02_export_excerpt.csv", header_row: 5, skip_rows: 0, time_column: "elapsed [s]", delimiter: ";", decimal: ",",
      columns: { T2: { quantity: "surface_temperature", unit: "K" }, U: { quantity: "voltage", unit: "V" }, I: { quantity: "current", unit: "mA" } } });
    expect(b.time.unit).toBe("s");
    expect(b.scalars).toEqual([
      { quantity: "status", value: "ok", unit: "", source: "the operator" },
      { quantity: "max_temperature", value: 38.4, unit: "degC", source: "logger peak" },
      { quantity: "temperature_end", value: 26.1, unit: "degC", source: "logger" },
    ]);
  });

  test("one column for two measurements, or the time column as a measurement, is caught before sending", () => {
    const f = initialForm(REQ, SPEC);
    Object.assign(f, { file: "x.csv", timeColumn: "t", columns: { surface_temperature: { column: "T", unit: "degC" }, voltage: { column: "T", unit: "V" }, current: { column: "t", unit: "A" } } });
    const m = missing(f, REQ, SPEC);
    expect(m).toContain("T is chosen for both Surface temperature and voltage");
    expect(m).toContain("current: t is the time column");
  });

  test("a refusal's recorded reasons are read from the 422 detail; upload ids follow the server's pattern", () => {
    expect(refusedReasons({ message: "The result was refused; the reasons are recorded", result_rejected: { record_id: "rrj_1", reasons: ["the result lacks voltage", "specimen x is not the requested specimen y"] } }))
      .toEqual(["the result lacks voltage", "specimen x is not the requested specimen y"]);
    expect(refusedReasons("plain")).toEqual([]);
    expect(newUploadId()).toMatch(/^[0-9a-f]{32}$/);
  });
});
