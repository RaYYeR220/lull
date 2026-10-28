import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

/** Generates the player's test media (skipped when current) and checks the bundle was built. */
export default function globalSetup() {
  const root = path.join(__dirname, "..");
  execFileSync(process.execPath, [path.join(root, "scripts", "make-fixtures.mjs")], { cwd: root, stdio: "inherit" });
  if (!existsSync(path.join(root, "public", "player.js"))) {
    throw new Error("public/player.js is missing: run `pnpm build:e2e` (it runs build:player) before the e2e tests");
  }
}
