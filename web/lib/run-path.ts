/**
 * The run id in a live run's address: /run/<id> or /run/<id>/report. The run pages are one
 * static shell each (next.config rewrites every id to it), so the id is read in the browser.
 */
export function runIdFromPath(pathname: string): string | null {
  const m = /^\/run\/([^/]+)(?:\/report)?\/?$/.exec(pathname);
  if (!m || m[1] === "report") return null;
  try {
    return decodeURIComponent(m[1]!);
  } catch {
    return null;
  }
}
