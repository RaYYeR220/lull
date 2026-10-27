/**
 * Formats seconds as m:ss.dd (or h:mm:ss.dd past an hour).
 * Rounds once up front so 59.999 s becomes 1:00.00, never 0:60.00.
 */
export function formatTimecode(seconds: number, digits = 2): string {
  const scale = 10 ** digits;
  const total = Math.round(Math.max(0, seconds) * scale) / scale;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total - h * 3600) / 60);
  const s = total - h * 3600 - m * 60;
  const ss = s.toFixed(digits).padStart(digits ? 3 + digits : 2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** A valid HTML duration string for <time dateTime>, e.g. PT71.04S. */
export function isoDuration(seconds: number): string {
  const s = Math.max(0, seconds);
  return `PT${Number(s.toFixed(3))}S`;
}
