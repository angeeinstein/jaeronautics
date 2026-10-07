/**
 * What arrived in a list since it was last drawn -- a new account, an
 * application, a payment -- so the page can mark it for a moment
 * (``ja-arrived`` in styles/global.css). Only within one ``scope``: another
 * filter, sort or page of the same list is a different list, not arrivals, and
 * the first time a list is drawn nothing in it is new.
 */
import { useEffect, useRef, useState } from 'react';

const NONE: ReadonlySet<string> = new Set();

export function useArrivals(scope: string | null, ids: readonly string[] | undefined): ReadonlySet<string> {
  const seen = useRef<{ scope: string; ids: ReadonlySet<string> } | null>(null);
  const [arrived, setArrived] = useState<ReadonlySet<string>>(NONE);
  // The ids as text: the same list drawn again is not a change.
  const listed = ids?.join('\n');
  useEffect(() => {
    if (scope === null || listed === undefined) return;
    const now = listed ? listed.split('\n') : [];
    const before = seen.current;
    if (before?.scope === scope) {
      const added = now.filter((id) => !before.ids.has(id));
      if (added.length) setArrived(new Set(added));
    } else {
      setArrived(NONE);
    }
    seen.current = { scope, ids: new Set(now) };
  }, [scope, listed]);
  return arrived;
}

/** The class for a row or card that just arrived, else none. */
export function arrivedClass(arrived: ReadonlySet<string>, id: string | number): string | undefined {
  return arrived.has(String(id)) ? 'ja-arrived' : undefined;
}
