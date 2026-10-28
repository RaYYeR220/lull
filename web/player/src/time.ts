/** m:ss (h:mm:ss past an hour), floored so the readout never runs ahead of the picture. */
export function clock(seconds: number): string {
  const s = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

const unit = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Words for a screen reader: "1 minute 5 seconds". */
export function spokenTime(seconds: number): string {
  const s = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const parts = [h ? unit(h, "hour") : "", m ? unit(m, "minute") : "", sec || (!h && !m) ? unit(sec, "second") : ""];
  return parts.filter(Boolean).join(" ");
}

/** Seconds with two decimals and a unit: "1.50 s". */
export const seconds2 = (s: number): string => `${s.toFixed(2)} s`;
