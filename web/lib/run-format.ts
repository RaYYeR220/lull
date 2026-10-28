import type { StageName } from "./api";

/** What each stage does, in the visitor's words. */
export const STAGE_COPY: Record<StageName, { name: string; what: string; doing: string }> = {
  listen: { name: "Listen", what: "Transcribes the speech and finds the pauses", doing: "Listening for speech and pauses" },
  watch: { name: "Watch", what: "Reads the frames for what a listener would miss", doing: "Watching the frames" },
  research: { name: "Research", what: "Checks how names and terms are spelled", doing: "Looking up names and terms" },
  plan: { name: "Plan", what: "Drafts lines that fit each pause", doing: "Drafting lines for the pauses" },
  schedule: { name: "Schedule", what: "Places each line in its pause", doing: "Placing lines in pauses" },
  finalize: { name: "Finalize", what: "Checks each line against the frames, then voices it", doing: "Checking each line against the frames" },
  publish: { name: "Publish", what: "Mixes the soundtrack and writes the files", doing: "Mixing and writing the files" },
};

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
const count = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** One short line of what a finished stage reported, or null when it reported nothing useful. */
export function stageDetail(stage: StageName, info: Record<string, unknown> | null): string | null {
  if (!info) return null;
  switch (stage) {
    case "listen": {
      const words = count(info.words);
      const pauses = count(info.windows) ?? count(info.gaps);
      if (words === null || pauses === null) return null;
      return `${plural(words, "word")}, ${plural(pauses, "pause")}`;
    }
    case "watch": {
      const n = count(info.events);
      return n === null ? null : plural(n, "visual event");
    }
    case "research": {
      if (info.skipped === true) return "Skipped";
      const n = count(info.terms);
      return n === null ? null : plural(n, "term");
    }
    case "plan": {
      const n = count(info.variants);
      return n === null ? null : plural(n, "draft");
    }
    case "schedule": {
      const n = count(info.placed);
      if (n === null) return null;
      const ext = Array.isArray(info.extended) ? info.extended.length : 0;
      return ext ? `${n} placed, ${ext} for extended` : `${n} placed`;
    }
    case "finalize": {
      const v = count(info.verified);
      const h = count(info.held);
      if (v === null) return null;
      return h ? `${v} verified, ${h} held` : `${v} verified`;
    }
    case "publish": {
      const n = count(info.files);
      return n === null ? null : plural(n, "file");
    }
  }
}

const MODEL_NAMES: Record<string, string> = {
  "nvidia/Nemotron-3-Ultra-550b-a55b": "Nemotron 3 Ultra",
  "nvidia/Nemotron-3.5-Super-VL": "Nemotron 3.5 Super VL",
  "nvidia/Nemotron-3_5-Lightning": "Nemotron 3.5 Lightning",
  "tavily-search": "Tavily search",
  "tavily-extract": "Tavily extract",
};

/** A model id as a short product name: "nvidia/Nemotron-3.5-Super-VL" → "Nemotron 3.5 Super VL". */
export function modelName(id: string): string {
  const known = MODEL_NAMES[id];
  if (known) return known;
  const bare = id.includes("/") ? id.slice(id.lastIndexOf("/") + 1) : id;
  return bare.replace(/[-_]+/g, " ").trim() || id;
}

/** True for the NVIDIA models (the rest are search lookups). */
export const isNvidiaModel = (id: string) => id.startsWith("nvidia/");

const ROLE_NAMES: Record<string, string> = {
  watch: "Watch",
  plan: "Plan",
  verify: "Verify",
  repair: "Repair",
  compress: "Shorten",
  remove: "Cut claim",
  research: "Research",
};

/** A call's role as a short verb. */
export function roleName(role: string): string {
  return ROLE_NAMES[role] ?? role.charAt(0).toUpperCase() + role.slice(1).replace(/_/g, " ");
}

/** Dollars: four decimals below a dollar (model calls cost fractions of a cent), two above. */
export function usd(v: number): string {
  const abs = Math.abs(v);
  return `$${abs >= 1 ? abs.toFixed(2) : abs.toFixed(4)}`;
}

/** Dollars for budgets: cents, or tenths of a cent when it is under one. */
export function usdShort(v: number): string {
  const abs = Math.abs(v);
  return `$${abs > 0 && abs < 0.01 ? abs.toFixed(3) : abs.toFixed(2)}`;
}

export const tokens = (n: number) => n.toLocaleString("en-US");

/** Seconds with two decimals, e.g. "2.31 s". */
export const secs = (v: number, digits = 2) => `${v.toFixed(digits)} s`;

/** A length as m:ss, e.g. 90 → "1:30". */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** File size in MB (mebibytes, as the API counts its limit): "12.4 MB", "30 MB". */
export function megabytes(bytes: number): string {
  const mb = (bytes / 2 ** 20).toFixed(1);
  return `${mb.endsWith(".0") ? mb.slice(0, -2) : mb} MB`;
}

/** A run's warning in the visitor's words, where the engine's own wording is too internal. */
export function noteText(kind: string, message: string, count: number | null): string {
  switch (kind) {
    case "undescribed": {
      const n = count ?? 1;
      return `${n} key ${n === 1 ? "moment stays" : "moments stay"} undescribed: the plan drafted no line for ${n === 1 ? "it" : "them"}.`;
    }
    case "research_skipped":
      return "Research was skipped, so names are spelled as they were heard and read.";
    case "clamped_word":
      return "The speech recogniser stretched a word over a pause; its end was moved back so the pause stays usable.";
    default:
      return message;
  }
}
