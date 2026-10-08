import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { Logs } from './Logs';

type LogsOut = Schemas['LogsOut'];

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/admin/logs';

function logs(overrides: Partial<LogsOut> = {}): LogsOut {
  return {
    items: [
      {
        id: 2,
        at: '2026-10-01T12:05:00Z',
        category: 'account',
        event_type: 'email_changed',
        actor: { user_id: 1, email: 'boss@example.org' },
        target: { user_id: 7, email: 'anna@example.org' },
        before: { email: 'old@example.org' },
        after: { email: 'anna@example.org' },
        details: null,
      },
      {
        id: 1,
        at: '2026-09-30T08:00:00Z',
        category: 'system',
        event_type: 'backup_made',
        actor: null,
        target: null,
        before: null,
        after: null,
        details: null,
      },
    ],
    page: 1,
    pages: 1,
    total: 2,
    categories: ['account', 'system'],
    about: null,
    accounts_linked: true,
    ...overrides,
  };
}

function show(route = '/admin/logs', answers: Answers = {}) {
  const fetched = mockFetch({ [API]: { body: logs() }, ...answers });
  renderPage(<Logs />, { route, path: '/admin/logs' });
  return fetched;
}

function lastQuery(calls: Request[]): string {
  const request = calls.filter((call) => new URL(call.url).pathname === API).at(-1);
  return request ? new URL(request.url).search : '';
}

describe('the log', () => {
  it('who did what to whom, each person opening their account', async () => {
    show();

    const table = await screen.findByRole('table', { name: 'Log entries' });
    const rows = within(table).getAllByRole('row');
    const changed = rows[1] ?? table;
    const backup = rows[2];
    expect(changed).toHaveTextContent(
      '01.10.2026 14:05Account / Email Changedboss@example.organna@example.org',
    );
    expect(within(changed).getByRole('link', { name: 'anna@example.org' })).toHaveAttribute(
      'href',
      '/admin/accounts/7',
    );
    expect(backup).toHaveTextContent('System / Backup Madethe system–');
    expect(screen.getByText('2 entries')).toBeInTheDocument();
  });

  it('what changed is folded until asked for', async () => {
    show();

    const toggle = await screen.findByRole('button', { name: 'Account / Email Changed' });
    expect(screen.queryByText(/old@example.org/)).toBeNull();
    await userEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(/old@example.org/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'System / Backup Made' })).toBeNull();
  });

  it('people are plain text for somebody who may not see accounts', async () => {
    show('/admin/logs', { [API]: { body: logs({ accounts_linked: false }) } });

    await screen.findByRole('table', { name: 'Log entries' });
    expect(screen.queryByRole('link', { name: 'anna@example.org' })).toBeNull();
  });
});

describe('narrowing it', () => {
  it('search and category follow into the request and the address', async () => {
    const { calls } = show();

    await userEvent.type(await screen.findByRole('textbox', { name: 'Search the log' }), 'anna');
    await waitFor(() => {
      expect(lastQuery(calls)).toBe('?q=anna&page=1');
    });
    await userEvent.click(screen.getByRole('combobox', { name: 'Category' }));
    await userEvent.click(screen.getByRole('option', { name: 'System' }));

    await waitFor(() => {
      expect(lastQuery(calls)).toBe('?q=anna&category=system&page=1');
    });
  });

  it('about one person, until asked for everybody', async () => {
    const { calls } = show('/admin/logs?user=7', {
      [API]: { body: logs({ about: { user_id: 7, email: 'anna@example.org' } }) },
    });

    expect(await screen.findByText(/Only what is about/)).toHaveTextContent('anna@example.org');
    expect(lastQuery(calls)).toBe('?user=7&page=1');
    await userEvent.click(screen.getByRole('button', { name: 'Everybody' }));

    await waitFor(() => {
      expect(lastQuery(calls)).toBe('?page=1');
    });
  });

  it('a link to the old page still works', async () => {
    const { calls } = show('/admin/logs?category=all&q=anna');

    await screen.findByRole('table', { name: 'Log entries' });
    expect(lastQuery(calls)).toBe('?q=anna&page=1');
  });

  it('nothing matching says so', async () => {
    show('/admin/logs?q=nobody', { [API]: { body: logs({ items: [], total: 0 }) } });

    expect(await screen.findByText('Nothing in the log matches these filters.')).toBeInTheDocument();
  });
});
