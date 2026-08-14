import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SRC_DIR = fileURLToPath(new URL("../src/", import.meta.url));

function sourceFiles(): string[] {
  return readdirSync(SRC_DIR)
    .filter((name) => name.endsWith(".ts"))
    .sort();
}

function sourceText(name: string): string {
  return readFileSync(new URL(`../src/${name}`, import.meta.url), "utf8");
}

function lineCount(value: string): number {
  return value.split(/\r?\n/).length;
}

describe("gateway architecture", () => {
  it("keeps index.ts as a small worker facade", () => {
    const source = sourceText("index.ts");
    expect(lineCount(source)).toBeLessThanOrEqual(120);
    expect(source).toContain("export default");
  });

  it("keeps feature modules within the agreed size budget", () => {
    const oversized = sourceFiles()
      .filter((name) => name !== "index.ts")
      .map((name) => ({ name, lines: lineCount(sourceText(name)) }))
      .filter(({ lines }) => lines > 800);

    expect(oversized).toEqual([]);
  });

  it("prevents feature modules from depending on the worker facade", () => {
    const offenders = sourceFiles()
      .filter((name) => name !== "index.ts")
      .filter((name) => /from\s+["']\.\/index["']/.test(sourceText(name)));

    expect(offenders).toEqual([]);
  });

  it("keeps the internal module graph acyclic", () => {
    const remaining = new Map(
      sourceFiles().map((name) => [
        name,
        new Set([...sourceText(name).matchAll(/from\s+["']\.\/([^"']+)["']/g)]
          .map((match) => `${match[1]}.ts`)
          .filter((dependency) => dependency !== name)),
      ]),
    );

    while (remaining.size) {
      const removable = [...remaining.entries()]
        .filter(([, dependencies]) => [...dependencies].every((dependency) => !remaining.has(dependency)))
        .map(([name]) => name);
      if (!removable.length) break;
      for (const name of removable) {
        remaining.delete(name);
      }
    }

    expect([...remaining.keys()]).toEqual([]);
  });
});
