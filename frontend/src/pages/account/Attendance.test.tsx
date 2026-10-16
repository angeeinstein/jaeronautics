import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { makeMe } from '../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { Attendance } from './Attendance';

afterEach(() => {
  vi.unstubAllGlobals();
});

const base: Answers = {
  '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
  '/api/v1/me': { body: makeMe({ attendance_area: 'menu' }) },
};

function stay(overrides: Partial<Schemas['StayOut']> = {}): Schemas['StayOut'] {
  return {
    id: 1,
    // 09:15:59 to 11:40:02 in Vienna: 2 h 25 min.
    started_at: '2026-10-14T07:15:59Z',
    ended_at: '2026-10-14T09:40:02Z',
    minutes: 145,
    state: 'done',
    in_place: 'Hangar door',
    out_place: 'Workshop',
    corrected_by: null,
    corrected_at: null,
    ...overrides,
  };
}

const forgotten = stay({
  id: 2,
  started_at: '2026-10-13T16:02:00Z',
  ended_at: null,
  minutes: null,
  state: 'not_checked_out',
});

function owner(overrides: Partial<Schemas['OwnerStaysOut']> = {}): Schemas['OwnerStaysOut'] {
  return {
    owner: 'rocket-team',
    name: 'Rocket Team',
    current: true,
    ask: 'team',
    stays: [stay(), forgotten],
    ...overrides,
  };
}

describe('my attendance', () => {
  it('stays of the month with their time in full minutes, and the total', async () => {
    mockFetch({
      ...base,
      '/api/v1/account/attendance': {
        body: {
          owners: [
            owner({ stays: [stay(), stay({ id: 3, corrected_by: 'Lena Lead', minutes: 60 }), forgotten] }),
          ],
        },
      },
    });
    renderPage(<Attendance />);

    const table = await screen.findByRole('table', { name: 'Stays at Rocket Team' });
    expect(table).toHaveTextContent('09:15');
    expect(table).toHaveTextContent('Hangar door');
    expect(table).toHaveTextContent('2 h 25 min');
    expect(table).toHaveTextContent('Corrected by Lena Lead');
    expect(table).toHaveTextContent('Not checked out');
    expect(screen.getByRole('region', { name: 'Rocket Team' })).toHaveTextContent('3 h 25 min');
  });

  it('asks the leads for a correction of a stay not checked out, the form filled in', async () => {
    mockFetch({ ...base, '/api/v1/account/attendance': { body: { owners: [owner()] } } });
    renderPage(<Attendance />);

    await userEvent.click(await screen.findByRole('button', { name: 'Ask for a correction' }));
    const dialog = await screen.findByRole('dialog', { name: 'Message the leads of Rocket Team' });
    expect(within(dialog).getByRole('textbox', { name: 'Subject' })).toHaveValue('Attendance on 13.10.2026');
    expect(within(dialog).getByRole('textbox', { name: 'Message' })).toHaveValue(
      'Please correct my stay on 13.10.2026: checked in at 18:02 (Hangar door), not checked out. I left at ',
    );
  });

  it("at the association's readers, the contact form instead; kept stays say so", async () => {
    mockFetch({
      ...base,
      '/api/v1/account/attendance': {
        body: {
          owners: [owner({ owner: 'association', name: 'Association', ask: 'contact', current: false })],
        },
      },
    });
    renderPage(<Attendance />);

    const link = await screen.findByRole('link', { name: 'Ask for a correction' });
    const target = new URL(link.getAttribute('href') ?? '', 'http://portal.test');
    expect(target.pathname).toBe('/contact');
    expect(target.searchParams.get('topic')).toBe('other');
    expect(target.searchParams.get('subject')).toBe('Attendance on 13.10.2026');
    expect(screen.getByText(/Kept for you/)).toBeInTheDocument();
  });
});
