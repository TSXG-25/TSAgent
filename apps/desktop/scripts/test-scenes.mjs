import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { spawn } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = fileURLToPath(new URL("..", import.meta.url));
const temporaryRoot = await mkdtemp(join(tmpdir(), "tsagent-scenes-tests-"));
const output = join(temporaryRoot, "scenesHarness.mjs");

try {
  await build({
    entryPoints: [join(desktopRoot, "tests/scenesHarness.ts")],
    bundle: true,
    format: "esm",
    platform: "node",
    outfile: output,
  });

  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [output], { cwd: desktopRoot, stdio: "inherit" });
    child.once("error", reject);
    child.once("exit", (code) => (code === 0 ? resolve() : reject(new Error(`scenes harness exited ${code}`))));
  });
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}
