import raw from "@/public/showcase/index.json";
import { parseIndex } from "./showcase-schema.mjs";
import type { ShowcaseIndexEntry } from "./showcase-schema.mjs";

/**
 * The recorded runs the site ships (public/showcase/index.json), bundled into the code, for the
 * pages rendered on request (a live run's page and studio): they read no files at run time.
 * The build checks the index against each showcase.json (lib/showcase.ts loadIndex).
 */
export const SHOWCASE_INDEX: readonly ShowcaseIndexEntry[] = parseIndex(raw);
