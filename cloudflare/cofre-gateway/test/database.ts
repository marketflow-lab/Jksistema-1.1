import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import { readFileSync } from "node:fs";

export function createDatabase() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec("PRAGMA foreign_keys=ON");
  for (const name of ["0001_company_state.sql", "0002_publishing.sql"]) sqlite.exec(readFileSync(new URL("../migrations/" + name, import.meta.url), "utf8"));
  const calls: { sql: string; values: SQLInputValue[] }[] = [];
  function prepare(sql: string, values: SQLInputValue[] = []): any {
    const execute = () => {
      const statement = sqlite.prepare(sql);
      const results = statement.columns().length ? statement.all(...values) : (statement.run(...values), []);
      return { results, success: true, meta: { changes: Number(sqlite.prepare("SELECT changes() n").get()?.n ?? 0) } };
    };
    return { bind(...bound: SQLInputValue[]) { calls.push({ sql, values: bound }); return prepare(sql, bound); },
      async first() { return execute().results[0] ?? null; }, async all() { return execute(); }, async run() { return execute(); }, execute };
  }
  const binding = { prepare, async batch(statements: any[]) {
    sqlite.exec("BEGIN");
    try { const results = statements.map(s => s.execute()); sqlite.exec("COMMIT"); return results; }
    catch (error) { sqlite.exec("ROLLBACK"); throw error; }
  } } as unknown as D1Database;
  return { sqlite, binding, calls };
}
