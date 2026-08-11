import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { spawn } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = fileURLToPath(new URL("..", import.meta.url));
const repositoryRoot = fileURLToPath(new URL("../../../", import.meta.url));
const temporaryRoot = await mkdtemp(join(tmpdir(), "tsagent-desktop4c-tests-"));
const output = join(temporaryRoot, "desktop4cE2EHarness.mjs");

try {
  await build({
    entryPoints: [join(desktopRoot, "tests/desktop4cE2EHarness.ts")],
    bundle: true,
    format: "esm",
    platform: "node",
    outfile: output,
  });

  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [output], {
      cwd: desktopRoot,
      env: { ...process.env, TSAGENT_REPO_ROOT: repositoryRoot },
      stdio: "inherit",
    });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Desktop-4c harness exited with code ${String(code)}`));
    });
  });
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}
