import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { Health } from './Health';
import { Updates } from './Updates';

vi.mock('../../../lib/reload', () => ({ reloadPage: vi.fn() }));
const { reloadPage } = await import('../../../lib/reload');

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.mocked(reloadPage).mockClear();
});

function show(element: React.ReactNode, answers: Answers, me = makeMe()) {
  // The very object the stub reads, so a test can change an answer midway.
  const all: Answers = {
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: me },
    ...answers,
  };
  const fetched = mockFetch(all);
  renderPage(element);
  return { ...fetched, answers: all };
}

const HEALTH = '/api/v1/admin/settings/health';

const healthy: Schemas['HealthOut'] = {
  healthy: true,
  problems: [],
  warnings: [],
  database_schema: { applied: 'abc123', expected: 'abc123', up_to_date: true },
  membership: {
    members: 40,
    currently_covered: 38,
    coverage_periods: 52,
    revoked_periods: 0,
    erased_members: 0,
  },
  queues: {
    external_work_pending: 0,
    external_work_failed: 0,
    webhook_events_completed: 120,
    webhook_events_failed: 0,
    emails_pending: 0,
    emails_overdue: 0,
    emails_exhausted: 0,
  },
  undelivered: [],
};

describe('system health', () => {
  it('all well, said so', async () => {
    show(<Health />, { [HEALTH]: { body: healthy } });

    expect(await screen.findByText('Nothing needs attention right now.')).toBeInTheDocument();
    expect(screen.getByText('Healthy')).toBeInTheDocument();
    expect(screen.getByText(/, up to date\./)).toBeInTheDocument();
  });

  it('problems first; tasks that gave up can be sent again', async () => {
    const { calls } = show(<Health />, {
      [HEALTH]: {
        body: {
          ...healthy,
          healthy: false,
          problems: ['3 background task(s) (forum, Stripe) gave up retrying.'],
          queues: { ...healthy.queues, external_work_failed: 3 },
        },
      },
      [`POST ${HEALTH}/forum-tasks/retry`]: { body: { count: 3 } },
    });

    expect(await screen.findByRole('alert')).toHaveTextContent('3 background task(s)');
    expect(screen.getByText('Needs attention')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByText('3 tasks will be tried again within a few minutes.')).toBeInTheDocument();
    expect(calls.some((call) => call.method === 'POST')).toBe(true);
  });

  it('an undelivered email: dismissing asks again', async () => {
    const { calls } = show(<Health />, {
      [HEALTH]: {
        body: {
          ...healthy,
          undelivered: [
            {
              id: 7,
              recipient: 'typo@exmaple.com',
              kind: 'welcome email',
              last_tried_at: '2026-10-01T08:00:00Z',
              error: 'Name or service not known',
            },
          ],
        },
      },
      [`POST ${HEALTH}/undelivered/7/dismiss`]: { body: { recipient: 'typo@exmaple.com' } },
    });

    const table = await screen.findByRole('table', { name: 'Undelivered emails' });
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent(
      'typo@exmaple.comwelcome email01.10.2026 10:00Name or service not known',
    );
    await userEvent.click(await within(table).findByRole('button', { name: 'Dismiss' }));
    expect(calls.some((call) => call.method === 'POST')).toBe(false);
    await userEvent.click(within(table).getByRole('button', { name: 'Yes, dismiss' }));

    expect(await screen.findByText('Dismissed the email to typo@exmaple.com.')).toBeInTheDocument();
  });

  it('without notifications.manage the emails are listed, not acted on', async () => {
    show(
      <Health />,
      {
        [HEALTH]: {
          body: {
            ...healthy,
            undelivered: [{ id: 7, recipient: 'a@example.org', kind: 'x', last_tried_at: null, error: null }],
          },
        },
      },
      makeMe({ permissions: ['admin.access', 'settings.general', 'system.update'] }),
    );

    await screen.findByRole('table', { name: 'Undelivered emails' });
    expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument();
  });
});

const UPDATES = '/api/v1/admin/settings/updates';

const upToDate: Schemas['UpdatesOut'] = {
  installed: {
    short_revision: 'aaaa1111',
    branch: 'main',
    rolled_back: false,
    committed_at: '2026-10-01T10:00:00Z',
    subject: 'Fix a thing',
  },
  latest: 'bbbb2222',
  latest_check_failed: false,
  newer_version: true,
  latest_check: {
    state: 'passed',
    url: null,
    started_at: null,
    minutes_running: null,
    typical_minutes: null,
  },
  update_available: true,
  runner_installed: true,
  in_progress: false,
  request_never_picked_up: false,
  progress: { steps_done: 0, steps_expected: null, percent: null, current_step: null, steps: [] },
  rollback_point: null,
  last_run: {
    state: null,
    finished_at: null,
    exit_code: null,
    revision_after: null,
    log_tail: null,
    interrupted: null,
  },
};

const running: Schemas['UpdatesOut'] = {
  ...upToDate,
  in_progress: true,
  progress: {
    steps_done: 3,
    steps_expected: 6,
    percent: 50,
    current_step: 'Building the front end',
    steps: [
      { label: 'Pausing background jobs for the update', state: 'done', detail: null },
      { label: 'Installing Python dependencies', state: 'done', detail: 'pip is slow today' },
      { label: 'Building the front end', state: 'running', detail: null },
      { label: 'Verifying deployment', state: 'pending', detail: null },
    ],
  },
  last_run: { ...upToDate.last_run, state: 'running', log_tail: '[STEP] Building the front end' },
};

describe('updates', () => {
  it('the version and the newest; installing asks again', async () => {
    const { calls } = show(<Updates />, { [UPDATES]: { body: upToDate } });

    expect(await screen.findByText('A newer version is available.')).toBeInTheDocument();
    expect(screen.getByText('aaaa1111')).toBeInTheDocument();
    expect(screen.getByText('bbbb2222')).toBeInTheDocument();
    expect(screen.getByText(/No rollback point has been recorded yet/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Install update now' }));
    expect(calls.some((call) => call.method === 'POST')).toBe(false);
  });

  it('a version CI is still checking is not offered yet', async () => {
    const checking: Schemas['UpdatesOut'] = {
      ...upToDate,
      update_available: false,
      latest_check: {
        state: 'running',
        url: null,
        started_at: null,
        minutes_running: null,
        typical_minutes: 12,
      },
    };
    show(<Updates />, { [UPDATES]: { body: checking } });

    expect(
      await screen.findByText(/A new version is being checked \(starting; usually about 12 minutes\)/),
    ).toBeInTheDocument();
    expect(screen.getByText('Being checked')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Install update now' })).toBeDisabled();
  });

  it('nor one whose checks failed', async () => {
    const failed: Schemas['UpdatesOut'] = {
      ...upToDate,
      update_available: false,
      latest_check: {
        state: 'failed',
        url: 'https://github.com/o/r/actions/runs/8',
        started_at: null,
        minutes_running: null,
        typical_minutes: null,
      },
    };
    show(<Updates />, { [UPDATES]: { body: failed } });

    expect(await screen.findByText(/did not pass its checks/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'See what failed' })).toHaveAttribute(
      'href',
      'https://github.com/o/r/actions/runs/8',
    );
    expect(screen.getByRole('button', { name: 'Install update now' })).toBeDisabled();
  });

  it('follows the update through the restart, then loads the new version', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { answers } = show(<Updates />, {
      [UPDATES]: { body: upToDate },
      [`POST ${UPDATES}`]: { body: running },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Install update now' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, install' }));
    expect(await screen.findByText('Update running')).toBeInTheDocument();
    const steps = screen.getByRole('list', { name: 'Update steps' });
    expect(
      within(steps)
        .getAllByRole('listitem')
        .map((line) => line.textContent),
    ).toEqual([
      'Pausing background jobs for the update: Done',
      'Installing Python dependencies: Donepip is slow today',
      'Building the front end: In progress',
      'Verifying deployment: Not started',
    ]);

    // The server goes away while it restarts: said, and asked again.
    answers[UPDATES] = { status: 502, body: { error: { code: 'down', message: 'Bad gateway', fields: {} } } };
    await act(() => vi.advanceTimersByTimeAsync(12_000));
    expect(await screen.findByText('The site is restarting…')).toBeInTheDocument();

    // Back, with the update done.
    answers[UPDATES] = {
      body: {
        ...upToDate,
        update_available: false,
        newer_version: false,
        latest_check: null,
        last_run: { ...upToDate.last_run, state: 'completed' },
      },
    };
    await act(() => vi.advanceTimersByTimeAsync(2_500));
    expect(await screen.findByText('Finished. Loading the new version…')).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    await waitFor(() => {
      expect(reloadPage).toHaveBeenCalledOnce();
    });
  });

  it('the terminal output, folded away until asked for', async () => {
    const { calls } = show(<Updates />, {
      [UPDATES]: {
        body: {
          ...upToDate,
          last_run: { ...upToDate.last_run, state: 'failed', exit_code: 1, log_tail: 'the end' },
          progress: {
            ...upToDate.progress,
            steps: [
              { label: 'Fetching the front end built by CI', state: 'done', detail: null },
              { label: 'Installing Python dependencies', state: 'failed', detail: 'No space left on device' },
            ],
          },
        },
      },
      [`${UPDATES}/log`]: {
        body: { text: '[STEP] Fetching\n[STEP] Installing\n[ERR] No space', cut: false },
      },
    });

    const steps = await screen.findByRole('list', { name: 'Steps of the last update' });
    expect(steps).toHaveTextContent('Installing Python dependencies: FailedNo space left on device');
    expect(calls.some((call) => call.url.endsWith('/log'))).toBe(false);

    await userEvent.click(screen.getByRole('button', { name: 'Show the terminal output' }));

    expect(await screen.findByText(/\[ERR\] No space/)).toBeInTheDocument();
  });

  it('a rollback point, offered', async () => {
    show(<Updates />, {
      [UPDATES]: {
        body: {
          ...upToDate,
          rollback_point: {
            short_revision: 'cccc3333',
            recorded_at: '2026-09-30T20:00:00Z',
            database_backup: null,
          },
        },
      },
    });

    expect(await screen.findByRole('button', { name: 'Roll back to cccc3333' })).toBeEnabled();
  });

  it('without the runner nothing can be started', async () => {
    show(<Updates />, { [UPDATES]: { body: { ...upToDate, runner_installed: false } } });

    expect(await screen.findByText(/the update runner is not installed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Install update now' })).toBeDisabled();
  });
});
