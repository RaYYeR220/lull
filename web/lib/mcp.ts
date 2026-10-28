import { API_BASE } from "./api";

/**
 * The MCP servers (lull.api.agents): hosted at <API>/mcp over streamable HTTP, and local over
 * stdio (`lull mcp`, the engine CLI). The tools as each serves them, for the pages that list them.
 */
export type McpTool = { name: string; args: string; does: string; hosted: boolean; local: boolean };

export const MCP_TOOLS: readonly McpTool[] = [
  { name: "list_samples", args: "", does: "The sample clips it can describe, with title, length, credit and licence.", hosted: true, local: true },
  {
    name: "describe_video",
    args: "sample_id, mode",
    does: "Describes a sample (locally, a file of yours by path) and returns the run id, its edit token, counts, coverage, WCAG statuses and spend. Blocks while it runs and sends progress.",
    hosted: true,
    local: true,
  },
  { name: "get_tracks", args: "run_id", does: "Every line with its timecode and status, and the files: tracks, the described audio and videos.", hosted: true, local: true },
  { name: "get_report", args: "run_id", does: "The conformance report: WCAG rows with what blocks each, held lines with reasons, spend.", hosted: true, local: true },
  { name: "review_queue", args: "", does: "Your runs with lines held for a person, newest first.", hosted: false, local: true },
];

/** The hosted endpoint. */
export const MCP_URL = `${API_BASE}/mcp`;

/** Claude Desktop and other JSON-configured clients, hosted (streamable HTTP). */
export const MCP_HOSTED_CONFIG = `{
  "mcpServers": {
    "lull": { "type": "http", "url": "${MCP_URL}" }
  }
}`;

/** Claude Desktop, local (stdio): your keys, your caps, your files. */
export const MCP_LOCAL_CONFIG = `{
  "mcpServers": {
    "lull": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/lull/engine", "lull", "mcp"],
      "env": { "NEBIUS_API_KEY": "your Token Factory key", "TAVILY_API_KEY": "your Tavily key" }
    }
  }
}`;
