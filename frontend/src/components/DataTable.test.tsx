import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { renderPage } from '../test/render';
import { columnsFor, DataTable, type Sort } from './DataTable';

interface Person {
  id: number;
  name: string;
  team: string;
}

const column = columnsFor<Person>();
const columns = column.columns([
  column.accessor('name', {
    header: 'Name',
    cell: ({ row, getValue }) => <a href={`/people/${String(row.original.id)}`}>{getValue()}</a>,
  }),
  column.accessor('team', { header: 'Team' }),
  column.display({ id: 'note', header: 'Note', cell: () => 'nothing' }),
]);

const people: Person[] = [
  { id: 1, name: 'Anna', team: 'Rocket' },
  { id: 2, name: 'Bernd', team: 'Glider' },
];

afterEach(() => {
  vi.unstubAllGlobals();
});

function show({ sort = { by: 'name', desc: false }, data = people, href = true } = {}) {
  const onSortChange = vi.fn<(sort: Sort) => void>();
  const assign = vi.fn();
  vi.stubGlobal('location', { origin: window.location.origin, href: window.location.href, assign });
  renderPage(
    <DataTable
      label="People"
      columns={columns}
      data={data}
      rowId={(row) => String(row.id)}
      sort={sort}
      onSortChange={onSortChange}
      rowHref={href ? (row) => `/people/${String(row.id)}` : undefined}
      empty="Nobody yet."
    />,
  );
  return { onSortChange, assign };
}

describe('the headings', () => {
  it('say which column the list is sorted by, and which way', () => {
    show({ sort: { by: 'team', desc: true } });

    const table = screen.getByRole('table', { name: 'People' });
    const [name, team, note] = within(table).getAllByRole('columnheader');
    expect(name).toHaveAttribute('aria-sort', 'none');
    expect(team).toHaveAttribute('aria-sort', 'descending');
    expect(note).not.toHaveAttribute('aria-sort');
    expect(within(screen.getByRole('columnheader', { name: 'Note' })).queryByRole('button')).toBeNull();
  });

  it('sort by their column: first one way, then the other', async () => {
    const { onSortChange } = show();

    await userEvent.click(screen.getByRole('button', { name: /Team/ }));
    expect(onSortChange).toHaveBeenLastCalledWith({ by: 'team', desc: false });

    await userEvent.click(screen.getByRole('button', { name: /Name/ }));
    expect(onSortChange).toHaveBeenLastCalledWith({ by: 'name', desc: true });
  });
});

describe('the rows', () => {
  it('open their record on a click anywhere in them', async () => {
    const { assign } = show();

    await userEvent.click(screen.getByText('Glider'));

    expect(assign).toHaveBeenCalledWith('/people/2');
  });

  it('leave a click on a link to the link', async () => {
    const { assign } = show();
    const link = screen.getByRole('link', { name: 'Anna' });
    link.addEventListener('click', (event) => {
      event.preventDefault();
    });

    await userEvent.click(link);

    expect(assign).not.toHaveBeenCalled();
  });

  it('do nothing on a click when they lead nowhere', async () => {
    const { assign } = show({ href: false });

    await userEvent.click(screen.getByText('Glider'));

    expect(assign).not.toHaveBeenCalled();
  });
});

it('an empty list is one sentence, not an empty table', () => {
  show({ data: [] });

  expect(screen.getByText('Nobody yet.')).toBeInTheDocument();
  expect(screen.queryByRole('table')).toBeNull();
});
