import { screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { mockFetch, renderPage } from '../../test/render';
import { AdminDashboard, tasksOf } from './AdminDashboard';

type Dashboard = Schemas['DashboardOut'];

afterEach(() => {
  vi.unstubAllGlobals();
});

const none = { count: 0, summary: null, at: null };

function dashboard(overrides: Partial<Dashboard> = {}): Dashboard {
  return {
    attention: { name_changes: none, pictures: none, sync_problems: none, health_problems: [] },
    figures: [
      {
        key: 'active',
        label: 'Active members',
        value: 214,
        note: 'of 230 accounts',
        link_url: '/admin/accounts?membership=active',
      },
      { key: 'forum', label: 'On the forum', value: 180, note: 'nobody still setting up', link_url: null },
    ],
    recent_activity: [
      {
        id: 1,
        category: 'account',
        event_type: 'email_changed',
        target: 'anna@example.org',
        at: '2026-07-01T12:05:00Z',
      },
    ],
    ...overrides,
  };
}

function show(data: Dashboard) {
  mockFetch({ '/api/v1/admin/dashboard': { body: data } });
  renderPage(<AdminDashboard />);
}

describe('what is waiting', () => {
  it('one task per kind with something waiting, oldest named', () => {
    const tasks = tasksOf({
      name_changes: { count: 2, summary: 'Anna Berger → Anna Huber', at: null },
      pictures: { count: 1, summary: 'Bernd Back', at: '2026-07-01T12:05:00Z' },
      sync_problems: none,
      health_problems: ['1 email(s) could not be delivered.', 'Backups are old.'],
    });

    expect(tasks.map((task) => [task.title, task.detail])).toEqual([
      ['Change requests to review', 'Oldest: Anna Berger → Anna Huber'],
      ['Profile picture to review', 'Bernd Back, uploaded 01.07.2026 14:05'],
      ['System health problems', '1 email(s) could not be delivered. And 1 more.'],
    ]);
  });

  it('a list with a way to each', async () => {
    show(
      dashboard({
        attention: {
          name_changes: { count: 1, summary: 'Anna Berger → Anna Huber', at: null },
          pictures: none,
          sync_problems: none,
          health_problems: [],
        },
      }),
    );
    const panel = await screen.findByRole('region', { name: 'Needs your attention' });

    expect(within(panel).getByRole('link', { name: 'Review' })).toHaveAttribute(
      'href',
      '/admin/reviews#review-queue',
    );
  });

  it('one all-clear line when nothing waits', async () => {
    show(dashboard());

    expect(await screen.findByText('Nothing needs your attention')).toBeInTheDocument();
    expect(screen.getByText('No reviews waiting, no forum or system problems.')).toBeInTheDocument();
  });

  it('neither for somebody who may act on none of it', async () => {
    show(
      dashboard({
        attention: { name_changes: null, pictures: null, sync_problems: null, health_problems: null },
      }),
    );
    await screen.findByText('Active members');

    expect(screen.queryByText('Nothing needs your attention')).toBeNull();
    expect(screen.queryByRole('region', { name: 'Needs your attention' })).toBeNull();
  });
});

describe('the figures and the log', () => {
  it('a figure with a list behind it is a link to it', async () => {
    show(dashboard());

    const active = await screen.findByRole('link', { name: /Active members/ });
    expect(active).toHaveAttribute('href', '/admin/accounts?membership=active');
    expect(screen.queryByRole('link', { name: /On the forum/ })).toBeNull();
  });

  it('the latest entries, in Vienna time', async () => {
    show(dashboard());

    expect(await screen.findByText('Account / Email Changed')).toBeInTheDocument();
    expect(screen.getByText('anna@example.org · 01.07.2026 14:05')).toBeInTheDocument();
  });

  it('no log for somebody who may not read it', async () => {
    show(dashboard({ recent_activity: null }));
    await screen.findByText('Active members');

    expect(screen.queryByRole('region', { name: 'Recent activity' })).toBeNull();
  });
});
