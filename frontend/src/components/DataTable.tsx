/**
 * A table of records (docs/frontend-structure.md, 7): small upper-case
 * headings, a line between rows, the whole row a way to the record it shows,
 * sideways scrolling on a phone.
 *
 * Built for lists the server sorts and pages -- every admin list is longer
 * than one screen, and sorting the rows on screen would put the wrong ones
 * there. TanStack Table (rowSortingFeature, manualSorting) keeps the columns
 * and the sort state; the page keeps the sort in its address and fetches the
 * rows in that order.
 *
 * A row with ``rowHref`` opens on a click anywhere in it; its first link is
 * the same address, for the keyboard, a screen reader and a middle click.
 */
import { Group, Table, UnstyledButton } from '@mantine/core';
import { IconArrowDown, IconArrowUp, IconArrowsSort } from '@tabler/icons-react';
import {
  createColumnHelper,
  rowSortingFeature,
  tableFeatures,
  useTable,
  type RowData,
  type TableOptions,
} from '@tanstack/react-table';
import type { MouseEvent } from 'react';

import { useGo } from '../app/useGo';
import { EmptyState } from './States';
import classes from './DataTable.module.css';

export const dataTableFeatures = tableFeatures({ rowSortingFeature });
type Features = typeof dataTableFeatures;

/** The column helper for a table of ``Row``s: ``const column = columnsFor<Row>()``. */
export function columnsFor<Row extends RowData>() {
  return createColumnHelper<Features, Row>();
}

/** One column, one direction: the lists sort by one thing at a time. */
export interface Sort<Key extends string = string> {
  by: Key;
  desc: boolean;
}

interface DataTableProps<Row extends RowData, Key extends string> {
  /** What the table lists, for screen readers ("Accounts"). */
  label: string;
  /** Kept stable: defined outside the component, or memoised. */
  columns: TableOptions<Features, Row>['columns'];
  data: Row[];
  rowId: (row: Row) => string;
  sort: Sort<Key>;
  onSortChange: (sort: Sort<Key>) => void;
  rowHref?: (row: Row) => string;
  /** Rows being replaced by the next ones: shown dimmed meanwhile. */
  busy?: boolean;
  /** One sentence for an empty list. */
  empty: string;
  /** Below this width the table scrolls sideways rather than squeezing its columns. */
  minWidth?: number;
}

const ARIA_SORT = { asc: 'ascending', desc: 'descending', none: 'none' } as const;

function SortIcon({ direction }: { direction: false | 'asc' | 'desc' }) {
  const Icon = direction === 'asc' ? IconArrowUp : direction === 'desc' ? IconArrowDown : IconArrowsSort;
  return <Icon size={14} stroke={2} aria-hidden className={direction ? undefined : classes.idle} />;
}

/** Whether a click landed on something that does its own thing (a link, a button). */
function onControl(event: MouseEvent) {
  const target = event.target as Element;
  return target.closest('a, button, input, select, textarea, [role="button"]') !== null;
}

export function DataTable<Row extends RowData, Key extends string>({
  label,
  columns,
  data,
  rowId,
  sort,
  onSortChange,
  rowHref,
  busy = false,
  empty,
  minWidth = 720,
}: DataTableProps<Row, Key>) {
  const go = useGo();
  const table = useTable({
    features: dataTableFeatures,
    columns,
    data,
    getRowId: (row) => rowId(row),
    manualSorting: true,
    enableMultiSort: false,
    enableSortingRemoval: false,
    state: { sorting: [{ id: sort.by, desc: sort.desc }] },
    onSortingChange: (updater) => {
      const current = [{ id: sort.by, desc: sort.desc }];
      const [next] = typeof updater === 'function' ? updater(current) : updater;
      if (next) onSortChange({ by: next.id as Key, desc: next.desc });
    },
  });

  if (!data.length) return <EmptyState>{empty}</EmptyState>;

  return (
    <Table.ScrollContainer minWidth={minWidth} type="native">
      <Table
        className={classes.table}
        highlightOnHover={rowHref !== undefined}
        verticalSpacing="sm"
        aria-label={label}
        aria-busy={busy}
        data-busy={busy || undefined}
      >
        <Table.Thead>
          {table.getHeaderGroups().map((group) => (
            <Table.Tr key={group.id}>
              {group.headers.map((header) => {
                const column = header.column;
                const title = <table.FlexRender header={header} />;
                if (!column.getCanSort()) {
                  return (
                    <Table.Th key={header.id} scope="col" className={classes.th}>
                      {title}
                    </Table.Th>
                  );
                }
                const direction = column.getIsSorted();
                return (
                  <Table.Th
                    key={header.id}
                    scope="col"
                    className={classes.th}
                    aria-sort={ARIA_SORT[direction || 'none']}
                  >
                    <UnstyledButton className={classes.sortButton} onClick={column.getToggleSortingHandler()}>
                      <Group gap={4} wrap="nowrap">
                        {title}
                        <SortIcon direction={direction} />
                      </Group>
                    </UnstyledButton>
                  </Table.Th>
                );
              })}
            </Table.Tr>
          ))}
        </Table.Thead>
        <Table.Tbody>
          {table.getRowModel().rows.map((row) => {
            const href = rowHref?.(row.original);
            return (
              <Table.Tr
                key={row.id}
                className={href ? classes.linkRow : undefined}
                onClick={
                  href
                    ? (event: MouseEvent) => {
                        if (onControl(event)) return;
                        go(href, { newTab: event.ctrlKey || event.metaKey });
                      }
                    : undefined
                }
              >
                {row.getAllCells().map((cell) => (
                  <Table.Td key={cell.id}>
                    <table.FlexRender cell={cell} />
                  </Table.Td>
                ))}
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
