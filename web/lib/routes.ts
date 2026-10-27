/** The page that plays a run with its descriptions (a recorded one, or a finished live one). */
export const watchHref = (id: string) => `/watch/${encodeURIComponent(id)}`;

/** The page that reviews and edits a run's cues. */
export const studioHref = (id: string) => `/studio/${encodeURIComponent(id)}`;
