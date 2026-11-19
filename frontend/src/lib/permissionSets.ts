/**
 * Ticking and unticking permissions that need others (docs/permissions-plan.md):
 * "every writing permission also requires the reading permission", coupled --
 * ticking one ticks what it needs, unticking one unticks what needs it. The
 * same rules as the server's (permissions.py, NEEDS; services/teams.py,
 * TEAM_NEEDS), which has the last word. Used by a person's Access tab and a
 * team member's page.
 */

/** What each permission needs, by name. */
export type Needs<T extends string = string> = Map<T, readonly T[]>;

/** ``names`` and everything they need. */
export function withNeeds<T extends string>(names: Iterable<T>, needs: Needs<T>): Set<T> {
  const result = new Set(names);
  const waiting = [...result];
  for (let name = waiting.pop(); name !== undefined; name = waiting.pop()) {
    for (const needed of needs.get(name) ?? []) {
      if (!result.has(needed)) {
        result.add(needed);
        waiting.push(needed);
      }
    }
  }
  return result;
}

/** ``names`` less any whose needs are not all among them. */
export function withoutUnmet<T extends string>(names: Iterable<T>, needs: Needs<T>): Set<T> {
  const result = new Set(names);
  for (let dropped = true; dropped;) {
    dropped = false;
    for (const name of result) {
      if ((needs.get(name) ?? []).some((needed) => !result.has(needed))) {
        result.delete(name);
        dropped = true;
      }
    }
  }
  return result;
}

/** ``names`` with ``name`` unticked, and whatever needed it. */
export function untick<T extends string>(names: Iterable<T>, name: T, needs: Needs<T>): Set<T> {
  return withoutUnmet(
    [...names].filter((other) => other !== name),
    needs,
  );
}

/** Options in the order given, grouped by their area. */
export function groupByArea<T extends { area: string }>(options: readonly T[]): [string, T[]][] {
  const areas = new Map<string, T[]>();
  for (const option of options) {
    const listed = areas.get(option.area);
    if (listed) listed.push(option);
    else areas.set(option.area, [option]);
  }
  return [...areas];
}
