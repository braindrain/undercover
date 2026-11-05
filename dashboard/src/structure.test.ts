import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

it("calls the backend's API only from api.ts", () => {
  const folder = import.meta.dirname;
  const sources = readdirSync(folder).filter(
    (name) => /\.tsx?$/.test(name) && !/\.test\.|^api\.ts$/.test(name),
  );
  expect(sources).toContain("ClaimPage.tsx");
  for (const name of sources)
    expect(readFileSync(join(folder, name), "utf8"), name).not.toMatch(
      /["'`]\/api\//,
    );
});

it("reaches the agent's service only through the backend", () => {
  const folder = import.meta.dirname;
  const sources = readdirSync(folder).filter(
    (name) => /\.tsx?$/.test(name) && !name.includes(".test."),
  );
  for (const name of sources)
    expect(readFileSync(join(folder, name), "utf8"), name).not.toMatch(
      /https?:\/\/|:8002/,
    );
  const config = readFileSync(join(folder, "..", "vite.config.ts"), "utf8");
  expect(config).not.toContain(":8002");
});
