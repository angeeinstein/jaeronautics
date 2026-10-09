import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { mockFetch, renderPage } from '../../../test/render';
import { minutesAgo, PresenceNote } from './Presence';

afterEach(() => {
  vi.unstubAllGlobals();
});

const nobody: Schemas['PresenceOut'] = {
  active: 0,
  signed_in: 0,
  visitors: 0,
  typing: 0,
  pages: [],
  last_seen_at: null,
};

function show(body: Schemas['PresenceOut']) {
  const fetched = mockFetch({ '/api/v1/admin/presence': { body } });
  renderPage(<PresenceNote />);
  return fetched;
}

describe('who is using the portal, on the Updates page', () => {
  it('nobody', async () => {
    const { calls } = show(nobody);
    expect(await screen.findByText('Nobody else is using the portal right now.')).toBeInTheDocument();
    // The asking tab leaves itself out.
    expect(new URL(calls[0]?.url ?? '').searchParams.get('tab')).toBeTruthy();
  });

  it('nobody now, somebody a little while ago', async () => {
    show({ ...nobody, last_seen_at: new Date(Date.now() - 5 * 60_000).toISOString() });
    expect(await screen.findByText(/last seen 5 minutes ago/)).toBeInTheDocument();
  });

  it('people, their pages, and a warning while somebody types', async () => {
    show({
      active: 3,
      signed_in: 1,
      visitors: 2,
      typing: 1,
      pages: [
        { page: '/join', people: 2, typing: 1 },
        { page: '/account', people: 1, typing: 0 },
      ],
      last_seen_at: new Date().toISOString(),
    });
    expect(
      await screen.findByText('3 people using the portal right now: 1 signed in, 2 visitors.'),
    ).toBeInTheDocument();
    expect(screen.getByText(/1 person is typing/)).toBeInTheDocument();
    expect(screen.getByText('/join')).toBeInTheDocument();
    expect(screen.getByText('2 people, 1 typing')).toBeInTheDocument();
  });

  it('minutes ago', () => {
    const now = Date.parse('2026-10-09T12:00:00Z');
    expect(minutesAgo('2026-10-09T11:59:30Z', now)).toBe('just now');
    expect(minutesAgo('2026-10-09T11:59:00Z', now)).toBe('1 minute ago');
    expect(minutesAgo('2026-10-09T11:48:00Z', now)).toBe('12 minutes ago');
  });
});
