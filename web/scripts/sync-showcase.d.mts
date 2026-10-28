/** Types for sync-showcase.mjs (imported by its tests). */
import type { ShowcaseIndexEntry } from "../lib/showcase-schema.mjs";

export declare const PUBLIC_SHOWCASE: string;
export declare class SyncError extends Error {
  constructor(message: string);
}
export declare function syncShowcase(opts: { src: string; id: string; dest?: string }): { files: string[]; index: ShowcaseIndexEntry[] };
export declare function writeIndex(dest?: string): ShowcaseIndexEntry[];
