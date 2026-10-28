/**
 * A refusal named by what it means where it happened, when its code alone would say it wrong:
 * a day's limit (a 429 whose message says "today"), too many people following runs (429
 * busy), or another change holding the run (409 busy in the studio). Null: the code's own
 * heading says it.
 */
export type RefusalContext = "start" | "follow" | "fork" | "change" | "publish";

const DAILY: Record<RefusalContext, string> = {
  start: "live descriptions",
  follow: "following runs",
  fork: "copies",
  change: "changes",
  publish: "publishes",
};

export function refusalWords(e: { code: string; status: number; message: string; apiCode?: string | null }, context: RefusalContext): string | null {
  // only the API's own refusals: a bare 409 from a proxy isn't another change holding the run
  if (e.apiCode === null) return null;
  if (e.code === "rate_limited" && /\btoday\b/i.test(e.message)) return `You’ve reached today’s limit for ${DAILY[context]}.`;
  if (e.code === "busy" && e.status === 429) return "Too many people are following this run right now.";
  if (e.code === "busy" && e.status === 409 && (context === "change" || context === "publish")) return "Another change to this run is in progress. Try again in a moment.";
  return null;
}
