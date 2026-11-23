import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { makeMe } from '../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { YourTime } from './YourTime';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const base: Answers = {
  '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
  '/api/v1/me': { body: makeMe({ attendance_area: 'menu' }) },
};

function stay(overrides: Partial<Schemas['StayOut']> = {}): Schemas['StayOut'] {
  return {
    id: 1,
    started_at: '2026-10-14T07:00:00Z',
    ended_at: '2026-10-14T09:00:00Z',
    minutes: 120,
    state: 'done',
    in_place: 'Hangar',
    out_place: 'Hangar',
    corrected_by: null,
    corrected_at: null,
    ...overrides,
  };
}

function attendance(remote: boolean, current = true) {
  return {
    body: {
      owners: [
        {
          owner: 'rocket',
          name: 'Rocket',
          current,
          ask: 'team',
          remote,
          stays: [stay(), stay({ id: 2, remote: true, minutes: 90, in_place: null, out_place: null })],
        },
      ],
      remote_from: '2026-10-06',
      today: '2026-10-20',
    },
  };
}

describe("one's time on the team's page", () => {
  it('this month, the remote part in small print, and adding remote work', async () => {
    mockFetch({ ...base, '/api/v1/account/attendance': attendance(true) });
    renderPage(<YourTime slug="rocket" member />);

    expect(await screen.findByText('Your time this month')).toBeInTheDocument();
    expect(screen.getByText('3 h 30 min')).toBeInTheDocument();
    expect(screen.getByText('2 h at the readers · 1 h 30 min remote')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add remote work' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Your time' })).toHaveAttribute('href', '/account/attendance');
  });

  it('without remote work, no button; not shown where attendance is not theirs now', async () => {
    mockFetch({ ...base, '/api/v1/account/attendance': attendance(false) });
    const { unmount } = renderPage(<YourTime slug="rocket" member />);
    expect(await screen.findByText('Your time this month')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add remote work' })).not.toBeInTheDocument();
    unmount();

    mockFetch({ ...base, '/api/v1/account/attendance': attendance(true, false) });
    renderPage(<YourTime slug="rocket" member />);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText('Your time this month')).not.toBeInTheDocument();
  });
});
