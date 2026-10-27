#!/usr/bin/env node
/**
 * Builds the interim showcase file from the approved design's data.json.
 *
 *   node scripts/showcase-from-v07.mjs <path/to/v07-final/data.json> [id]
 *
 * Writes public/showcase/<id>/showcase.json (id defaults to ed90) in the schema of
 * lib/showcase.ts, marked "interim": true. Speech, pauses, loudness and words come from the
 * real 90-second run; cue placement is estimated here: each line starts 0.25 s into its
 * pause and lasts words / 2.36 s (the measured voice rate), never past the pause. Cue
 * statuses are the design's illustrative ones. The engine's `lull export-showcase`
 * replaces this file with measured values in the same schema.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WPS = 2.36; // measured words per second of the voice
const LEAD = 0.25; // where a line starts inside its pause, seconds
const FLOOR_DB = -120;
// the design's illustrative statuses, by pause
const STATUS = { g001: "verified", g002: "edited", g003: "verified", g004: "held" };

const [src, id = "ed90"] = process.argv.slice(2);
if (!src) {
  console.error("usage: node scripts/showcase-from-v07.mjs <path/to/data.json> [id]");
  process.exit(2);
}
if (!/^[a-z0-9-]+$/.test(id)) throw new Error(`bad showcase id: ${id}`);

const v07 = JSON.parse(readFileSync(src, "utf8"));
const r2 = (x) => Math.round(x * 100) / 100;
const words = (s) => s.trim().split(/\s+/).length;

/** The draft with every claim the frames did not support taken out, as the verifier does. */
function spokenLine(draft, claims) {
  let rest = draft;
  for (const c of claims) {
    if (c.verdict === "supported") continue;
    const i = rest.toLowerCase().indexOf(c.text.toLowerCase());
    if (i < 0) throw new Error(`claim not in draft: ${c.text}`);
    // drop the claim with the comma or space that led into it
    const head = rest.slice(0, i).replace(/[,;:]?\s*$/, "");
    rest = head + rest.slice(i + c.text.length);
  }
  rest = rest.replace(/\s+([.,;:!?])/g, "$1").replace(/\s{2,}/g, " ").trim();
  return rest.charAt(0).toUpperCase() + rest.slice(1);
}

const gaps = v07.gaps.map((g) => ({ id: g.id, start: g.start, end: g.end, capacity: g.capacity_words }));
const gapById = new Map(gaps.map((g) => [g.id, g]));

// The verify example is the draft of the first cue (the design labels it "Draft, cue 1"),
// so that cue speaks what is left of it once the unsupported claim is cut.
const verify = v07.verify_example;
const firstSpoken = verify ? spokenLine(verify.cue, verify.claims) : null;

const cues = v07.sample_cues.map((c, i) => {
  const g = gapById.get(c.gap);
  if (!g) throw new Error(`cue for unknown pause ${c.gap}`);
  const text = i === 0 && firstSpoken ? firstSpoken : c.text;
  const start = r2(g.start + LEAD);
  const end = r2(Math.min(start + words(text) / WPS, g.end));
  return {
    id: `c${String(i + 1).padStart(3, "0")}`,
    start,
    end,
    text,
    status: STATUS[c.gap] ?? "verified",
    ahead: false,
  };
});

const [title, credit] = (() => {
  // "Elephants Dream (2006), Blender Foundation, CC BY 2.5 — 01:00–02:30"
  const credit = v07.source.split(" — ")[0].trim();
  return [credit.replace(/\s*\(.*$/, ""), credit];
})();

const showcase = {
  id,
  title,
  credit,
  duration: v07.duration,
  loudness: v07.loudness_db_100ms.map((db) => Math.max(FLOOR_DB, Math.round(db * 10) / 10)),
  speech: v07.speech,
  gaps,
  cues,
  words: v07.words.map((w) => ({ text: w.t, start: w.s, end: w.e })),
  ...(verify
    ? { verifyExample: { cue: verify.cue, claims: verify.claims.map((c) => ({ text: c.text, verdict: c.verdict })) } }
    : {}),
  ...(typeof v07.cost_90s_usd === "number" ? { costUsd: v07.cost_90s_usd } : {}),
  interim: true,
};

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.join(here, "..", "public", "showcase", id);
mkdirSync(outDir, { recursive: true });
const out = path.join(outDir, "showcase.json");
writeFileSync(out, JSON.stringify(showcase) + "\n");
console.log(`wrote ${path.relative(process.cwd(), out)}: ${cues.length} cues, ${gaps.length} pauses, ${showcase.loudness.length} loudness frames`);
