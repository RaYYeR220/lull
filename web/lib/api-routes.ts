/**
 * Every API route the site calls, in one table: the client builds its URLs from it and the
 * developer reference documents each entry (lib/api-reference.ts is typed against it, so a
 * route can't be called without being documented).
 */

export const ROUTES = {
  health: { method: "GET", path: "/v1/health" },
  samples: { method: "GET", path: "/v1/samples" },
  startRun: { method: "POST", path: "/v1/runs" },
  listRuns: { method: "GET", path: "/v1/runs" },
  run: { method: "GET", path: "/v1/runs/{id}" },
  original: { method: "GET", path: "/v1/runs/{id}/original" },
  events: { method: "GET", path: "/v1/runs/{id}/events" },
  file: { method: "GET", path: "/v1/runs/{id}/files/{path}" },
  cueAudio: { method: "GET", path: "/v1/runs/{id}/cues/{cue_id}/audio" },
  editCue: { method: "PATCH", path: "/v1/runs/{id}/cues/{cue_id}" },
  approveCue: { method: "POST", path: "/v1/runs/{id}/cues/{cue_id}/approve" },
  holdCue: { method: "POST", path: "/v1/runs/{id}/cues/{cue_id}/hold" },
  publish: { method: "POST", path: "/v1/runs/{id}/publish" },
  fork: { method: "POST", path: "/v1/runs/{id}/fork" },
  mcp: { method: "POST", path: "/mcp" },
} as const;

export type RouteName = keyof typeof ROUTES;

type Params = { id?: string; cue_id?: string; path?: string };

/**
 * A route's path with its parameters filled in: ids encoded, a file path encoded segment by
 * segment (its slashes kept). A parameter left out stays as its {placeholder}, which is how
 * the player's per-cue pattern keeps "{id}" for itself.
 */
export function routePath(name: RouteName, params: Params = {}): string {
  return ROUTES[name].path.replace(/\{(id|cue_id|path)\}/g, (whole, key: keyof Params) => {
    const v = params[key];
    if (v === undefined) return whole;
    return key === "path" ? v.split("/").map(encodeURIComponent).join("/") : encodeURIComponent(v);
  });
}
