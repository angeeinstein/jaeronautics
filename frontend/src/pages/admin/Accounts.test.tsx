import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { mockFetch, renderPage } from '../../test/render';
import { Accounts } from './Accounts';

type AccountList = Schemas['AccountListOut'];
type Row = Schemas['AccountRow'];

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function row(overrides: Partial<Row> = {}): Row {
  return {
    id: 7,
    email: 'anna@example.org',
    name: 'Anna Berger',
    year_group: '2023',
    category: 'student',
    category_label: 'Student',
    old_forum: null,
    old_forum_username: null,
    account_state: 'active',
    disabled_reason: null,
    forum_username: 'BergerA_L23',
    roles: [],
    membership: 'active',
    membership_until: '2026-12-31',
    picture_url: null,
    ...overrides,
  };
}

function list(overrides: Partial<AccountList> = {}): AccountList {
  return {
    items: [row()],
    total: 1,
    page: 1,
    pages: 1,
    per_page: 50,
    membership_counts: { all: 3, active: 1, ending: 0, pending: 1, failed: 0, ended: 0, none: 1 },
    role_choices: [
      { slug: 'admin', label: 'Admin' },
      { slug: 'treasurer', label: 'Treasurer' },
    ],
    ...overrides,
  };
}

function show(data: AccountList = list(), route = '/admin/accounts') {
  const { calls } = mockFetch({ '/api/v1/admin/accounts': { body: data } });
  renderPage(<Accounts />, { route, path: '/admin/accounts' });
  /** The query the latest request asked for. */
  const asked = () => Object.fromEntries(new URL(calls.at(-1)?.url ?? 'http://x').searchParams);
  return { calls, asked };
}

describe('a row', () => {
  it('says who it is, what kind, the membership and until when', async () => {
    show();

    const table = await screen.findByRole('table', { name: 'Accounts' });
    const cells = within(table).getAllByRole('cell');
    expect(within(table).getByRole('link', { name: 'Anna Berger' })).toHaveAttribute(
      'href',
      '/admin/accounts/7',
    );
    expect(cells[0]).toHaveTextContent('anna@example.org');
    expect(cells[1]).toHaveTextContent('Student · 2023');
    expect(cells[2]).toHaveTextContent('Active');
    expect(cells[3]).toHaveTextContent('31.12.2026');
    expect(cells[4]).toHaveTextContent('BergerA_L23');
  });

  it('marks somebody from the old forum, and an account that cannot be used', async () => {
    show(
      list({
        items: [
          row({
            id: 8,
            name: 'Alen Popovic',
            email: 'a.popovic@old.example',
            category: null,
            category_label: null,
            year_group: 'LAV23',
            old_forum: 'unclaimed',
            old_forum_username: 'PopovicA_L23',
            membership: 'none',
            membership_until: null,
            forum_username: null,
          }),
          row({
            id: 9,
            name: 'Bernd Back',
            account_state: 'disabled',
            roles: [{ slug: 'admin', label: 'Admin' }],
          }),
        ],
      }),
    );

    const table = await screen.findByRole('table', { name: 'Accounts' });
    const [, archive, barred] = within(table).getAllByRole('row');
    expect(archive).toHaveTextContent('Old forum');
    expect(archive).toHaveTextContent('LAV23');
    expect(barred).toHaveTextContent('Deactivated');
    expect(barred).toHaveTextContent('Admin');
  });

  it('without a name shows the address in its place', async () => {
    show(list({ items: [row({ name: null, category_label: null, year_group: null, membership: 'none' })] }));

    expect(await screen.findByRole('link', { name: 'anna@example.org' })).toBeInTheDocument();
  });
});

describe('the filters', () => {
  it('membership as chips, each with how many it shows', async () => {
    show();

    const chips = await screen.findByRole('radiogroup', { name: 'Membership' });
    expect(await within(chips).findByRole('radio', { name: 'Payment pending 1' })).toBeInTheDocument();
    expect(within(chips).getByRole('radio', { name: 'No membership 1' })).toBeInTheDocument();
    expect(within(chips).getByRole('radio', { name: 'All 3' })).toBeChecked();
  });

  it('a chip asks for its part of the list, from the first page', async () => {
    const { asked } = show(list(), '/admin/accounts?page=2');
    await screen.findByRole('table');

    await userEvent.click(screen.getByRole('radio', { name: /Ending/ }));

    await waitFor(() => {
      expect(asked()).toMatchObject({ membership: 'ending', page: '1' });
    });
  });

  it('the search asks once typing stops', async () => {
    const { calls, asked } = show();
    await screen.findByRole('table');
    const before = calls.length;

    await userEvent.type(screen.getByRole('textbox', { name: 'Search accounts' }), 'anna');

    await waitFor(() => {
      expect(asked()).toMatchObject({ q: 'anna' });
    });
    expect(calls.length - before).toBe(1);
  });

  it('come from the address, old links included', async () => {
    const { asked } = show(list(), '/admin/accounts?active=active&role=role:treasurer&sort=member&dir=desc');

    await screen.findByRole('table');

    expect(asked()).toMatchObject({ membership: 'active', role: 'treasurer', sort: 'name', dir: 'desc' });
  });

  it('can be reset, keeping the sort', async () => {
    const { asked } = show(list(), '/admin/accounts?kind=archived&sort=forum&dir=desc');
    await screen.findByRole('table');

    await userEvent.click(screen.getByRole('button', { name: 'Reset' }));

    await waitFor(() => {
      expect(asked()).toMatchObject({ kind: 'portal', sort: 'forum', dir: 'desc' });
    });
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull();
  });

  it('leave the old forum out unless it is asked for', async () => {
    const { asked } = show();
    await screen.findByRole('table');

    expect(asked()).toMatchObject({ kind: 'portal' });
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull();
  });

  it('a search offers the old forum accounts that match too', async () => {
    const { asked } = show(list({ old_forum_matching: 2 }), '/admin/accounts?q=popovic');

    expect(await screen.findByText('2 old forum accounts match too.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Show them' }));

    await waitFor(() => {
      expect(asked()).toMatchObject({ q: 'popovic', kind: 'all' });
    });
  });
});

describe('sorting and pages', () => {
  it('a heading sorts the whole list on the server', async () => {
    const { asked } = show();
    await screen.findByRole('table');

    await userEvent.click(screen.getByRole('button', { name: /Paid until/ }));

    await waitFor(() => {
      expect(asked()).toMatchObject({ sort: 'until', dir: 'asc' });
    });
  });

  it('the pages, and how many accounts there are', async () => {
    const { asked } = show(list({ total: 120, pages: 3 }));

    expect(await screen.findByText('120 accounts')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Page 2' }));

    await waitFor(() => {
      expect(asked()).toMatchObject({ page: '2' });
    });
  });
});

it('an empty list says whether filters are to blame', async () => {
  show(list({ items: [], total: 0 }), '/admin/accounts?q=nobody');

  expect(await screen.findByText('No accounts match these filters.')).toBeInTheDocument();
});

it('a list that could not be loaded says so', async () => {
  mockFetch({
    '/api/v1/admin/accounts': {
      status: 400,
      body: {
        error: { code: 'validation_error', message: 'There is no such role.', fields: null, details: null },
      },
    },
  });
  renderPage(<Accounts />, { route: '/admin/accounts?role=emperor', path: '/admin/accounts' });

  expect(await screen.findByRole('alert')).toHaveTextContent('There is no such role.');
  await act(async () => {
    await Promise.resolve();
  });
});
