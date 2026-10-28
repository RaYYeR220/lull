/**
 * The runs this browser started or copied, kept in localStorage: the service lists no uploads
 * (or copies of them), so this is how a visitor finds their own again. Each row is the run's
 * id, a title, what kind of run it is and when it was made; the edit token stays under its own
 * key (lib/api.ts). Storage that is blocked or broken reads as empty and never throws.
 */
export type OwnRun = { id: string; title: string; kind: "sample" | "upload" | "fork"; made: number };

const KEY = "lull-own-runs";
const MAX = 20;
const KINDS = new Set(["sample", "upload", "fork"]);
/** What the service keeps, when its health hasn't said: uploads 7 days, copies 24 hours. */
export const DEFAULT_RETENTION = { uploadDays: 7, forkHours: 24 };

function read(): OwnRun[] {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(KEY) ?? "[]");
    if (!Array.isArray(raw)) return [];
    return raw.filter(
      (r): r is OwnRun =>
        typeof r === "object" && r !== null && typeof r.id === "string" && typeof r.title === "string" && KINDS.has(r.kind) && typeof r.made === "number",
    );
  } catch {
    return [];
  }
}

function write(rows: OwnRun[]): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(rows.slice(0, MAX)));
  } catch {
    // private mode or full: the run still works, it just isn't listed here later
  }
}

/** Notes a run this browser started or copied (again: it moves to the top). */
export function rememberRun(run: OwnRun): void {
  write([run, ...read().filter((r) => r.id !== run.id)]);
}

export function forgetRun(id: string): void {
  write(read().filter((r) => r.id !== id));
}

/**
 * This browser's runs, newest first, without those the service has removed by now: uploads
 * after `uploadDays`, copies after `forkHours` (a sample's runs are kept).
 */
export function ownRuns(now = Date.now(), keep: { uploadDays: number | null; forkHours: number | null } = DEFAULT_RETENTION): OwnRun[] {
  const upload = (keep.uploadDays ?? DEFAULT_RETENTION.uploadDays) * 86_400_000;
  const fork = (keep.forkHours ?? DEFAULT_RETENTION.forkHours) * 3_600_000;
  return read()
    .filter((r) => (r.kind === "upload" ? now - r.made < upload : r.kind === "fork" ? now - r.made < fork : true))
    .sort((a, b) => b.made - a.made);
}
