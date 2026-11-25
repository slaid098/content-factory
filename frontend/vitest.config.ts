import { readFileSync } from "node:fs";

import { defineConfig } from "vitest/config";

const page = readFileSync(new URL("./index.html", import.meta.url), "utf8");

export default defineConfig({
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: {
        html: page,
        url: "http://localhost/",
      },
    },
    include: ["tests/unit/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.ts"],
      reporter: ["text", "json-summary"],
      thresholds: {
        lines: 70,
        branches: 70,
        functions: 70,
        statements: 70,
      },
    },
  },
});
