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

    const table = await screen.findByRole('table', { name: 'Your time with Rocket Team' });
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

  it('remote work: what was done, the split, and the member changes their own', async () => {
    const remote = stay({
      id: 4,
      // 18:00 to 20:30 in Vienna.
      started_at: '2026-10-14T16:00:00Z',
      ended_at: '2026-10-14T18:30:00Z',
      minutes: 150,
      in_place: null,
      out_place: null,
      remote: true,
      note: 'Wing rib CAD, revision 3',
      may_change: true,
    });
    mockFetch({
      ...base,
      '/api/v1/account/attendance': {
        body: {
          owners: [owner({ remote: true, stays: [remote, stay()] })],
          remote_from: '2026-10-06',
          today: '2026-10-20',
        },
      },
    });
    renderPage(<Attendance />);

    const table = await screen.findByRole('table', { name: 'Your time with Rocket Team' });
    expect(table).toHaveTextContent('Remote');
    expect(table).toHaveTextContent('Wing rib CAD, revision 3');
    const panel = screen.getByRole('region', { name: 'Rocket Team' });
    expect(panel).toHaveTextContent('4 h 55 min');
    expect(panel).toHaveTextContent('2 h 25 min at the readers · 2 h 30 min remote');
    expect(within(table).getByRole('button', { name: 'Change' })).toBeInTheDocument();

    await userEvent.click(within(panel).getByRole('button', { name: 'Add remote work' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add remote work for Rocket Team' });
    expect(within(dialog).getByRole('textbox', { name: /What did you do/ })).toBeInTheDocument();
  });

  it('remote work marked as not counted: crossed out, by whom and why, no longer changed', async () => {
    const marked = stay({
      id: 5,
      minutes: 120,
      in_place: null,
      out_place: null,
      remote: true,
      note: 'Landing gear FEA',
      not_counted_at: '2026-10-15T08:00:00Z',
      not_counted_by: 'Lena Lead',
      not_counted_reason: 'Logged twice',
      may_change: false,
    });
    mockFetch({
      ...base,
      '/api/v1/account/attendance': {
        body: {
          owners: [owner({ remote: true, stays: [marked] })],
          remote_from: '2026-10-06',
          today: '2026-10-20',
        },
      },
    });
    renderPage(<Attendance />);

    const table = await screen.findByRole('table', { name: 'Your time with Rocket Team' });
    expect(table).toHaveTextContent('Not counted');
    expect(table).toHaveTextContent('by Lena Lead: Logged twice');
    expect(within(table).queryByRole('button', { name: 'Change' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Rocket Team' })).toHaveTextContent('0 min');
  });
});
