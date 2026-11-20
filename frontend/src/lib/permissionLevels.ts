/**
 * Permissions as rows with levels (docs/permissions-plan.md, "The page with
 * levels"): "None · View · Manage", "None · Install". Picking a level raises
 * the rows it needs ("every writing permission also requires the reading
 * permission"); lowering one lowers whatever needed it. The server says what
 * each level needs (api/permission_rows.py) and has the last word.
 */

export interface LevelRow<T extends string = string> {
  key: string;
  area: string;
  /** The levels above None: what each holds, lower ones included, and what it needs of other rows. */
  levels: readonly { permissions: readonly T[]; needs: Readonly<Record<string, number>> }[];
}

/** The level chosen for each row, by its key (0: none). */
export type Levels = Readonly<Record<string, number>>;

function needsOf(rows: readonly LevelRow[], key: string, level: number): Readonly<Record<string, number>> {
  if (level <= 0) return {};
  return rows.find((row) => row.key === key)?.levels[level - 1]?.needs ?? {};
}

function met(rows: readonly LevelRow[], levels: Levels, key: string, level: number): boolean {
  return Object.entries(needsOf(rows, key, level)).every(([needed, min]) => (levels[needed] ?? 0) >= min);
}

/** Every row lowered to the highest level whose needs are met. */
export function settle(rows: readonly LevelRow[], levels: Levels): Levels {
  const result: Record<string, number> = { ...levels };
  for (let changed = true; changed;) {
    changed = false;
    for (const row of rows) {
      let level = result[row.key] ?? 0;
      while (level > 0 && !met(rows, result, row.key, level)) level -= 1;
      if (level !== (result[row.key] ?? 0)) {
        result[row.key] = level;
        changed = true;
      }
    }
  }
  return result;
}

/** ``key`` set to ``level``, with what it needs raised and what needed more lowered. */
export function pick(rows: readonly LevelRow[], levels: Levels, key: string, level: number): Levels {
  const result: Record<string, number> = { ...levels, [key]: level };
  const raise = (row: string, at: number) => {
    for (const [needed, min] of Object.entries(needsOf(rows, row, at))) {
      if ((result[needed] ?? 0) < min) {
        result[needed] = min;
        raise(needed, min);
      }
    }
  };
  raise(key, level);
  return settle(rows, result);
}

/** The permissions the chosen levels hold, for saving. */
export function permissionsOf<T extends string>(rows: readonly LevelRow<T>[], levels: Levels): T[] {
  const held = new Set<T>();
  for (const row of rows) {
    const level = levels[row.key] ?? 0;
    for (const permission of level > 0 ? (row.levels[level - 1]?.permissions ?? []) : [])
      held.add(permission);
  }
  return [...held].sort();
}

/** The level each row is at now, by key. */
export function levelsOf(rows: readonly { key: string; level: number }[]): Levels {
  return Object.fromEntries(rows.map((row) => [row.key, row.level]));
}

/** Rows in the order given, grouped by their area. */
export function groupByArea<R extends { area: string }>(rows: readonly R[]): [string, R[]][] {
  const areas = new Map<string, R[]>();
  for (const row of rows) {
    const listed = areas.get(row.area);
    if (listed) listed.push(row);
    else areas.set(row.area, [row]);
  }
  return [...areas];
}

export function sameLevels(a: Levels, b: Levels): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].every((key) => (a[key] ?? 0) === (b[key] ?? 0));
}
