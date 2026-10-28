import { defineConfig } from "vitest/config";

export default defineConfig({
  // the view tests render components to markup: the automatic JSX runtime, as Next uses
  oxc: { jsx: { runtime: "automatic" } },
  resolve: { alias: { "@": new URL(".", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1") } },
  test: {
    include: ["player/**/*.test.ts", "test/**/*.test.ts", "test/**/*.test.tsx"],
    environment: "node",
  },
});
