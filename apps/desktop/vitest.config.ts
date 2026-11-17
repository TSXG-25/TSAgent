import { defineConfig } from "vitest/config";

// Unit tests run in node; component tests opt into jsdom per file with
// a `// @vitest-environment jsdom` docblock.
export default defineConfig({
  test: {
    include: ["tests/**/*.test.{ts,tsx}"],
    environment: "node",
  },
});
