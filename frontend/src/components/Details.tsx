/**
 * Facts about one thing, label beside value (docs/frontend-structure.md, 7);
 * on a phone the label goes above. An empty value shows as a dash, so a
 * missing fact reads as missing rather than as a gap in the layout.
 */
import type { ReactNode } from 'react';

import classes from './Details.module.css';

export type Detail = [label: string, value: ReactNode];

/** ``compact``: a narrow label column, for a card half the page wide. */
export function Details({ items, compact = false }: { items: Detail[]; compact?: boolean }) {
  return (
    <dl className={classes.list} data-compact={compact || undefined}>
      {items.map(([label, value]) => (
        <div key={label} className={classes.row}>
          <dt className={classes.label}>{label}</dt>
          <dd className={classes.value}>
            {value === null || value === undefined || value === '' ? '–' : value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
