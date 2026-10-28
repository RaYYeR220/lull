import { readFile } from "node:fs/promises";
import path from "node:path";
import { cache } from "react";
import { LAYOUT, SHOWCASE_ID, ShowcaseError, indexEntry, parseIndex, parseShowcase, showcaseUrl } from "./showcase-schema.mjs";
import type { Showcase, ShowcaseIndexEntry } from "./showcase-schema.mjs";

export type { CueStatus, Showcase, ShowcaseCue, ShowcaseGap, ShowcaseIndexEntry, ShowcaseSummary, ShowcaseWord, Verdict, VerifyExample } from "./showcase-schema.mjs";
export { parseShowcase };

/** Where the shipped showcases live: public/showcase/<id>/, listed in public/showcase/index.json. */
const root = () => path.join(process.cwd(), "public", "showcase");

/** Reads public/showcase/<id>/showcase.json at build time. Cached per request. */
export const loadShowcase = cache(async (id: string): Promise<Showcase> => {
  if (!SHOWCASE_ID.test(id)) throw new ShowcaseError(id, "id", "must be lowercase letters, digits and dashes");
  const raw: unknown = JSON.parse(await readFile(path.join(root(), id, LAYOUT.json), "utf8"));
  return parseShowcase(raw, id);
});

/**
 * public/showcase/index.json, the list of showcases the site ships (written by
 * scripts/sync-showcase.mjs). Each row is checked against its showcase.json, so a stale
 * index fails the build instead of showing numbers the run doesn't have.
 */
export const loadIndex = cache(async (): Promise<ShowcaseIndexEntry[]> => {
  const index = parseIndex(JSON.parse(await readFile(path.join(root(), "index.json"), "utf8")));
  const shows = await Promise.all(index.map((e) => loadShowcase(e.id)));
  index.forEach((entry, i) => {
    const show = shows[i];
    if (!show || JSON.stringify(entry) !== JSON.stringify(indexEntry(show)))
      throw new Error(`showcase index: the row for ${entry.id} doesn't match its showcase.json; run scripts/sync-showcase.mjs again`);
  });
  return index;
});

/** The files a showcase's player reads, as site URLs (the export's fixed layout). */
export function showcaseMedia(s: Pick<Showcase, "id" | "audio" | "cueAudioBase">) {
  return {
    video: showcaseUrl(s.id, LAYOUT.video),
    descriptions: showcaseUrl(s.id, LAYOUT.descriptions),
    extended: showcaseUrl(s.id, LAYOUT.extended),
    describedAudio: s.audio?.described,
    cueAudioBase: s.cueAudioBase,
    cueAudioExt: LAYOUT.cueAudioExt,
  };
}

/** The evidence frame the engine kept for a cue. */
export const keyframeUrl = (id: string, cueId: string) => showcaseUrl(id, `${LAYOUT.keyframes}${cueId}.jpg`);
