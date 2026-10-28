import { LIMITS, type Sample } from "./api";
import type { Recorded } from "./recorded";
import { clock } from "./run-format";

/** A sample as the try page offers it. */
export type SampleItem = { id: string; title: string; credit: string | null; duration: number | null; recorded: boolean };

/**
 * The try page's sample cards: each sample's own title, length, credit and licence as the API
 * describes it (GET /v1/samples). A sample that is also a recorded run on this site links to
 * it, and lends its facts where the API gives none; otherwise the id stands alone.
 */
export function sampleItems(samples: readonly Sample[], recorded: readonly Recorded[]): SampleItem[] {
  const byId = new Map(recorded.map((r) => [r.id, r]));
  return samples.map((s) => {
    const r = byId.get(s.id);
    const credit = s.credit || s.licence ? [s.credit, s.licence].filter(Boolean).join(", ") : (r?.credit ?? null);
    return { id: s.id, title: s.title ?? r?.title ?? s.id, credit, duration: s.duration ?? r?.duration ?? null, recorded: !!r };
  });
}

/** Why a sample can't be described live, when its length is over what the API takes for samples. */
export function sampleTooLong(s: Pick<SampleItem, "duration">): string | null {
  if (s.duration === null || s.duration <= LIMITS.maxSampleSeconds) return null;
  return `This sample is ${clock(s.duration)} long; the live service takes samples up to ${clock(LIMITS.maxSampleSeconds)}.`;
}
