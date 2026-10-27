import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["player/**/*.test.ts", "test/**/*.test.ts"],
    environment: "node",
  },
});
