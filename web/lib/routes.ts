/** The page that plays a recorded run with its descriptions. */
export const watchHref = (id: string) => `/watch/${encodeURIComponent(id)}`;

/** The page that plays a finished live run, loaded from the API. */
export const runHref = (id: string) => `/run/${encodeURIComponent(id)}`;

/** The try-it page following a live run (replays it, then follows it while it goes). */
export const followHref = (id: string) => `/try?run=${encodeURIComponent(id)}`;

/** The page that reviews and edits a run's cues. */
export const studioHref = (id: string) => `/studio/${encodeURIComponent(id)}`;
