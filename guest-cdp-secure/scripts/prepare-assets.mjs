import { copyFile, mkdir, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const source = path.join(root, "guest_cdp_dashboard.html");
const targetDir = path.join(root, "guest-cdp-secure", "public");
const target = path.join(targetDir, "dashboard.html");

const info = await stat(source);
if (info.size < 100_000) throw new Error("Dashboard source is unexpectedly small");
await mkdir(targetDir, { recursive: true });
await copyFile(source, target);
console.log(`Prepared protected dashboard: ${(info.size / 1024 / 1024).toFixed(1)} MiB`);
