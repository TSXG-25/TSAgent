import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { spawn } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = fileURLToPath(new URL("..", import.meta.url));
const temporaryRoot = await mkdtemp(join(tmpdir(), "tsagent-orbits-tests-"));
const output = join(temporaryRoot, "orbitsHarness.mjs");

try {
  await build({
    entryPoints: [join(desktopRoot, "tests/orbitsHarness.ts")],
    bundle: true,
    format: "esm",
    platform: "node",
    outfile: output,
  });

  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [output], { cwd: desktopRoot, stdio: "inherit" });
    child.once("error", reject);
    child.once("exit", (code) => (code === 0 ? resolve() : reject(new Error(`orbits harness exited ${code}`))));
  });
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}
