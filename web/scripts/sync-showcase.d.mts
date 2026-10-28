/** Types for sync-showcase.mjs (imported by its tests). */
import type { ShowcaseIndexEntry } from "../lib/showcase-schema.mjs";

export declare const PUBLIC_SHOWCASE: string;
export declare class SyncError extends Error {
  constructor(message: string);
}
export declare const MAX_VIDEO_BYTES: number;
export declare const MAX_480_BYTES: number;
export declare const MAX_DESCRIBED_BYTES: number;
export declare const MAX_POSTER_BYTES: number;
export declare function reencodeArgs(from: string, to: string, lines?: number, sound?: "copy" | "aac64"): string[];
export declare function audioArgs(from: string, to: string): string[];
export declare function posterArgs(from: string, to: string, q: number): string[];
export type MediaTools = {
  video: (from: string, to: string, lines: number, sound: "copy" | "aac64") => void;
  audio: (from: string, to: string) => void;
  poster: (from: string, to: string) => void;
};
export declare const FFMPEG: MediaTools;
export type SyncedVideo = { bytes: number; reencoded: false } | { bytes: number; reencoded: true; from: number; lines: number };
export declare function syncShowcase(opts: {
  src: string;
  id: string;
  dest?: string;
  maxVideoBytes?: number;
  max480Bytes?: number;
  maxDescribedBytes?: number;
  tools?: MediaTools;
}): {
  files: string[];
  index: ShowcaseIndexEntry[];
  video: SyncedVideo;
  described: { bytes: number; from: number } | null;
  poster: { bytes: number } | { error: string };
};
export declare function writeIndex(dest?: string): ShowcaseIndexEntry[];
export declare function isInside(outer: string, inner: string): boolean;
