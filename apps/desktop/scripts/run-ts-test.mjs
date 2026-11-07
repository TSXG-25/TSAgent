// Bundles each TypeScript test entry with esbuild and runs it with Node.
// The entries import `node:test` themselves. Usage: node scripts/run-ts-test.mjs tests/a.test.ts [...]
import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { spawn } from "node:child_process";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = fileURLToPath(new URL("..", import.meta.url));
const repositoryRoot = fileURLToPath(new URL("../../../", import.meta.url));
const entries = process.argv.slice(2);
if (entries.length === 0) throw new Error("usage: node scripts/run-ts-test.mjs <test-entry.ts>...");

const temporaryRoot = await mkdtemp(join(tmpdir(), "tsagent-desktop-tests-"));
try {
  for (const entry of entries) {
    const output = join(temporaryRoot, basename(entry).replace(/\.ts$/, ".mjs"));
    await build({
      entryPoints: [join(desktopRoot, entry)],
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
        else reject(new Error(`${entry} exited with code ${String(code)}`));
      });
    });
  }
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}
