import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { Backup } from './Backup';
import { SettingsIndex } from './SettingsIndex';

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/admin/settings/backup';

const empty: Schemas['BackupOut'] = {
  paused: null,
  jobs: [],
  checklist: null,
  run: null,
  backups: [],
  min_passphrase_length: 12,
  kept: 5,
};

function show(answers: Answers) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    ...answers,
  });
  renderPage(<Backup />);
  return fetched;
}

function bodyOf(calls: Request[], method: string, path: string) {
  return calls
    .filter((call) => call.method === method && new URL(call.url).pathname === path)
    .at(-1)
    ?.clone()
    .json();
}

describe('backup and restore', () => {
  it('a backup: the two passphrases, and what the server says about them at their field', async () => {
    const { calls } = show({
      [API]: { body: empty },
      [`POST ${API}`]: {
        status: 400,
        body: {
          error: {
            code: 'passphrases_differ',
            message: 'The two passphrases are not the same.',
            fields: { passphrase_again: 'The two passphrases are not the same.' },
            details: null,
          },
        },
      },
    });

    await userEvent.type(await screen.findByLabelText('Passphrase'), 'a long passphrase');
    await userEvent.type(screen.getByLabelText('Passphrase again'), 'another one');
    await userEvent.click(screen.getByRole('button', { name: 'Back up' }));

    expect(await screen.findByText('The two passphrases are not the same.')).toBeInTheDocument();
    expect(await bodyOf(calls, 'POST', API)).toEqual({
      passphrase: 'a long passphrase',
      passphrase_again: 'another one',
    });
  });

  it('one being made: its steps; nothing else can start', async () => {
    show({
      [API]: {
        body: {
          ...empty,
          run: {
            state: 'running',
            steps: [
              { key: 'database', label: 'Copying the database', state: 'done' },
              { key: 'files', label: 'Adding uploaded files', state: 'running' },
            ],
            log: ['10:00:01  Copying'],
            error: null,
            result: null,
            started_at: '2026-10-07T08:00:00Z',
            finished_at: null,
          },
        },
      },
    });

    const steps = await screen.findByRole('list', { name: 'Making the backup' });
    const [database, files] = within(steps).getAllByRole('listitem');
    expect(database).toHaveTextContent('Copying the database: Done');
    expect(files).toHaveTextContent('Adding uploaded files: In progress');
    expect(screen.getByRole('button', { name: 'Back up' })).toBeDisabled();
  });

  it('the backups kept: downloaded from the server, deleted after asking again', async () => {
    const { calls } = show({
      [API]: {
        body: {
          ...empty,
          backups: [
            {
              name: 'portal-backup-20261007-080000.jabackup',
              size: 1300000,
              size_display: '1.2 MB',
              made_at: '2026-10-07T08:00:00Z',
            },
          ],
        },
      },
      [`DELETE ${API}/files/portal-backup-20261007-080000.jabackup`]: {
        body: { name: 'portal-backup-20261007-080000.jabackup' },
      },
    });

    const table = await screen.findByRole('table', { name: 'Backups on this server' });
    expect(within(table).getByRole('link', { name: 'Download' })).toHaveAttribute(
      'href',
      '/admin/backup/files/portal-backup-20261007-080000.jabackup',
    );
    expect(screen.getByRole('region', { name: 'Restore' })).toHaveTextContent(
      'sudo ./install.sh --restore /path/to/portal-backup-20261007-080000.jabackup',
    );
    await userEvent.click(within(table).getByRole('button', { name: 'Delete' }));
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false);
    await userEvent.click(within(table).getByRole('button', { name: 'Yes, delete' }));

    expect(await screen.findByText('Deleted portal-backup-20261007-080000.jabackup.')).toBeInTheDocument();
  });

  it('a restored portal: resumed only once somebody says it is the real one', async () => {
    const { calls } = show({
      [API]: {
        body: {
          ...empty,
          paused: { since: '2026-10-07T07:00:00Z', backup_created_at: '2026-10-01T10:00:00Z' },
          jobs: [
            { key: 'external-work', label: 'Forum and account queue', state: 'scheduled', detail: 'Later.' },
          ],
        },
      },
      [`POST ${API}/resume`]: { body: { ...empty, checklist: [] } },
      [`${API}/checklist`]: { body: { items: [], done: true, paused: false } },
    });

    expect(await screen.findByText(/restored from a backup made on 01\.10\.2026 12:00/)).toBeInTheDocument();
    const resume = screen.getByRole('button', { name: 'Resume background jobs' });
    expect(resume).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox', { name: /This is the server members use/ }));
    await userEvent.click(resume);

    expect(await screen.findByText('Background jobs resumed.')).toBeInTheDocument();
    expect(await bodyOf(calls, 'POST', `${API}/resume`)).toEqual({ confirm: true });
    expect(await screen.findByRole('region', { name: 'Is everything working?' })).toBeInTheDocument();
  });
});

describe('/admin/settings', () => {
  function Where() {
    return <p>{useLocation().pathname}</p>;
  }

  it.each([
    ['', '/admin/settings/general'],
    ['#settings-forum', '/admin/settings/forum'],
    ['#backup-restore', '/admin/settings/backup'],
    ['#settings-maintenance', '/admin/settings/health'],
  ])('%s opens %s', async (hash, page) => {
    const router = createMemoryRouter(
      [
        { path: '/admin/settings', element: <SettingsIndex /> },
        { path: '/admin/settings/:section', element: <Where /> },
      ],
      { initialEntries: [`/admin/settings${hash}`] },
    );
    render(<RouterProvider router={router} />);

    await waitFor(() => {
      expect(screen.getByText(page)).toBeInTheDocument();
    });
  });
});
