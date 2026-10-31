// Build step (prebuild/predev): split each intervention bundle in public/fixtures/<key>.json into
// public/fixtures/<key>/summary.json and public/fixtures/<key>/trials/<Exp>.json, so the page never loads the 19 MB
// receipt. The original file is read only, never changed: it stays the verifiable receipt. Skips a bundle whose
// .stamp matches its SHA-256 and the splitter source.
//
//   bun scripts/split-intervention.ts                          # every registered intervention bundle
//   bun scripts/split-intervention.ts --if-present             # the same, or nothing when no bundle is there (prebuild:
//                                                              # the web image is built without fixtures; deploy seeds
//                                                              # and splits them on the fixtures volume)
//   bun scripts/split-intervention.ts --fixtures <dir>         # a fixtures folder elsewhere (deploy/seed-ledger.sh)
import { Database } from "bun:sqlite";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { INTERVENTION_KEYS, interventionEntry } from "../lib/intervention";
import { splitIntervention, type ReceiptRecord, type SplitOutput } from "../lib/intervention-split";

const argAfter = (flag: string) => { const i = process.argv.indexOf(flag); return i > 0 ? process.argv[i + 1] : undefined; };
const FIXTURES = resolve(argAfter("--fixtures") ?? join(import.meta.dir, "..", "public", "fixtures"));
/** Bounded decompression: a receipt part larger than this is refused. */
const MAX_INFLATE = 512 * 1024 * 1024;

const inflate = (b64: string) => gunzipSync(Buffer.from(b64, "base64"), { maxOutputLength: MAX_INFLATE });

/** The receipt's planner and fit records (the rest of the ledger is not needed by the page). */
function receiptRecords(receipt: { ledger?: { data?: string } } | null | undefined): ReceiptRecord[] {
  if (!receipt?.ledger?.data) return [];
  const db = Database.deserialize(inflate(receipt.ledger.data), true);
  try {
    const out: ReceiptRecord[] = [];
    for (const row of db.query("select body from record where kind = 'intervention'").all() as { body: string }[]) {
      const r = JSON.parse(row.body);
      if (r.type === "planner" || r.type === "fit") out.push({ id: r.id, type: r.type, seq: r.seq, data: r.data });
    }
    return out;
  } finally {
    db.close();
  }
}

/** Split one bundle file in memory. A registered comparator bundle (entry.comparator) is split and merged in. */
export function splitFile(key: string, path = join(FIXTURES, `${key}.json`)): SplitOutput {
  const entry = interventionEntry(key);
  const comparator = entry.key === key && entry.comparator ? { key: entry.comparator, out: splitFile(entry.comparator) } : undefined;
  const raw = readFileSync(path);
  const sha256 = createHash("sha256").update(raw).digest("hex");
  const bundle = JSON.parse(raw.toString("utf-8"));
  const blobs: Record<string, { data: string }> = bundle.intervention?.receipt?.blobs ?? {};
  const series = (artifact: string) => {
    const b = blobs[artifact];
    if (!b) return null;
    const s = JSON.parse(inflate(b.data).toString("utf-8"));
    const y = s.temperature_C ?? s.y;
    return Array.isArray(s.t) && Array.isArray(y) ? { t: s.t as number[], y: y as number[] } : null;
  };
  return splitIntervention({
    fixture: key,
    bundle,
    records: receiptRecords(bundle.intervention?.receipt),
    series,
    file: { path: `web/public/fixtures/${key}.json`, sha256, bytes: raw.length },
    comparator,
  });
}

/** The bundle digest, its comparator's, and the splitter source digest: any changing re-splits. */
function stamp(key: string): string {
  const h = createHash("sha256").update(readFileSync(join(FIXTURES, `${key}.json`)));
  const cmp = interventionEntry(key).comparator;
  if (cmp) h.update(readFileSync(join(FIXTURES, `${cmp}.json`)));
  for (const f of ["../lib/intervention.ts", "../lib/intervention-split.ts", "split-intervention.ts"]) h.update(readFileSync(join(import.meta.dir, f)));
  return h.digest("hex");
}

function writeSplit(key: string, out: SplitOutput) {
  const dir = join(FIXTURES, key);
  rmSync(join(dir, "trials"), { recursive: true, force: true });
  mkdirSync(join(dir, "trials"), { recursive: true });
  for (const t of out.trials) writeFileSync(join(dir, "trials", `${t.key}.json`), JSON.stringify(t));
  writeFileSync(join(dir, "summary.json"), JSON.stringify(out.summary));
  writeFileSync(join(dir, ".stamp"), stamp(key));
}

function upToDate(key: string): boolean {
  const file = join(FIXTURES, key, ".stamp");
  return existsSync(file) && existsSync(join(FIXTURES, key, "summary.json")) && readFileSync(file, "utf-8") === stamp(key);
}

if (import.meta.main) {
  // Fixtures absent altogether (the web image's build context): nothing to split, and the page fetches /fixtures/...
  // from the volume at run time. A partial set is still an error.
  if (process.argv.includes("--if-present") && !INTERVENTION_KEYS.some((key) => existsSync(join(FIXTURES, `${key}.json`)))) {
    console.log(`intervention: no bundles in ${FIXTURES}; skipping the split (deploy/seed-ledger.sh splits them on the fixtures volume)`);
    process.exit(0);
  }
  for (const key of INTERVENTION_KEYS) {
    const src = join(FIXTURES, `${key}.json`);
    if (!existsSync(src)) throw new Error(`Missing intervention bundle ${src}`);
    if (upToDate(key)) { console.log(`intervention ${key}: up to date`); continue; }
    const out = splitFile(key, src);
    writeSplit(key, out);
    console.log(`intervention ${key}: summary + ${out.trials.length} trials`);
  }
}
