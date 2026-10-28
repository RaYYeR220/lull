/**
 * Features that exist only once they are built. A page mentions one only when its flag is on,
 * so the site never describes something a visitor can't use.
 */

/** The MCP server: hosted at <API>/mcp (list_samples, describe_video, get_tracks, get_report) and local (`lull mcp`, adds files and review_queue). */
export const MCP_AVAILABLE = true;

/** A course library Lull watches, drafting new lectures as they arrive: not built yet. */
export const LIBRARY_AVAILABLE = false;
