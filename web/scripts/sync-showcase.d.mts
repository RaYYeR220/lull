/** Types for sync-showcase.mjs (imported by its tests). */
import type { ShowcaseIndexEntry } from "../lib/showcase-schema.mjs";

export declare const PUBLIC_SHOWCASE: string;
export declare class SyncError extends Error {
  constructor(message: string);
}
export declare const MAX_VIDEO_BYTES: number;
export declare function reencodeArgs(from: string, to: string): string[];
export type SyncedVideo = { bytes: number; reencoded: false } | { bytes: number; reencoded: true; from: number };
export declare function syncShowcase(opts: {
  src: string;
  id: string;
  dest?: string;
  maxVideoBytes?: number;
  encode?: (from: string, to: string) => void;
}): { files: string[]; index: ShowcaseIndexEntry[]; video: SyncedVideo };
export declare function writeIndex(dest?: string): ShowcaseIndexEntry[];
export declare function isInside(outer: string, inner: string): boolean;
