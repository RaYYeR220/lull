#!/usr/bin/env node
/**
 * The production build the e2e tests run against: the same as `pnpm build`, pointed at a local
 * API address (http://localhost:8000 unless NEXT_PUBLIC_API_URL says otherwise), which the
 * try-it tests route to the scripted stand-in (e2e/mock-api.mjs).
 *
 *   node scripts/build-e2e.mjs
 */
import { spawnSync } from "node:child_process";

const env = { ...process.env, NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000" };
const r = spawnSync("pnpm", ["build"], { stdio: "inherit", env, shell: process.platform === "win32" });
process.exit(r.status ?? 1);
