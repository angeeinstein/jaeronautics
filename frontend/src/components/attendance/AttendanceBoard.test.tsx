import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { Attendance } from '../../pages/admin/Attendance';
import { makeMe } from '../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { periodOf } from './AttendanceBoard';

afterEach(() => {
  vi.unstubAllGlobals();
});

const base: Answers = {
  '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
  '/api/v1/me': { body: makeMe() },
};

function overview(overrides: Partial<Schemas['AttendanceOut']> = {}): Schemas['AttendanceOut'] {
  return {
    enabled: true,
    start: '2026-10-01',
    end: '2026-10-15',
    rows: [
      { user_id: 7, name: 'Anna Active', minutes: 205, stays: 3, not_checked_out: 1 },
      { user_id: 8, name: 'Carla Member', minutes: 0, stays: 0, not_checked_out: 0 },
    ],
    present: [
      { stay_id: 9, user_id: 8, name: 'Carla Member', since: '2026-10-15T06:30:00Z', place: 'Club room' },
    ],
    people: [
      { user_id: 7, name: 'Anna Active' },
      { user_id: 8, name: 'Carla Member' },
    ],
    ...overrides,
  };
}

const forgotten: Schemas['StayOut'] = {
  id: 4,
  started_at: '2026-10-13T16:02:00Z',
  ended_at: null,
  minutes: null,
  state: 'not_checked_out',
  in_place: 'Club room',
  out_place: null,
  corrected_by: null,
  corrected_at: null,
};

describe('running attendance', () => {
  it('who is here now, and hours per person for the period', async () => {
    mockFetch({ ...base, '/api/v1/admin/attendance': { body: overview() } });
    renderPage(<Attendance />);

    expect(await screen.findByRole('region', { name: 'Here now' })).toHaveTextContent('Carla Member');
    const hours = screen.getByRole('table', { name: 'Hours per person' });
    const anna = within(hours).getByRole('row', { name: /Anna Active/ });
    expect(anna).toHaveTextContent('3 h 25 min');
    expect(anna).toHaveTextContent('1');
    expect(screen.getByRole('link', { name: 'CSV' })).toHaveAttribute(
      'href',
      '/api/v1/admin/attendance.csv?start=2026-10-01&end=2026-10-15',
    );
  });

  it('a stay is added for anybody who may check in, not only those with one already', async () => {
    mockFetch({
      ...base,
      '/api/v1/admin/attendance': {
        body: overview({
          rows: [{ user_id: 7, name: 'Anna Active', minutes: 205, stays: 3, not_checked_out: 1 }],
          people: [
            { user_id: 7, name: 'Anna Active' },
            { user_id: 11, name: 'Bruno New' },
          ],
        }),
      },
    });
    renderPage(<Attendance />);

    await userEvent.click(await screen.findByRole('button', { name: 'Add a stay' }));
    const form = await screen.findByRole('dialog', { name: 'Add a stay' });
    await userEvent.click(within(form).getByRole('combobox', { name: 'Person' }));
    expect(await screen.findByRole('option', { name: 'Bruno New' })).toBeInTheDocument();
  });

  it("a person's stay not checked out, given its check-out by hand", async () => {
    const { calls } = mockFetch({
      ...base,
      '/api/v1/admin/attendance': { body: overview() },
      '/api/v1/admin/attendance/people/7': { body: { user_id: 7, name: 'Anna Active', stays: [forgotten] } },
      'PUT /api/v1/admin/attendance/stays/4': { body: { ...forgotten, state: 'done' } },
    });
    renderPage(<Attendance />);

    await userEvent.click(await screen.findByRole('button', { name: 'Anna Active' }));
    const person = await screen.findByRole('dialog', { name: 'Anna Active' });
    expect(await within(person).findByText('Not checked out')).toBeInTheDocument();
    await userEvent.click(within(person).getByRole('button', { name: 'Change' }));
    const form = await screen.findByRole('dialog', { name: 'Change a stay of Anna Active' });
    // No check-out yet: the button waits for one.
    expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled();
    await userEvent.type(within(form).getByRole('spinbutton', { name: 'Check-out hours' }), '23');
    await userEvent.type(within(form).getByRole('spinbutton', { name: 'Check-out minutes' }), '40');
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PUT')).toBe(true);
    });
    const sent = calls.filter((call) => call.method === 'PUT').at(-1);
    // 18:02 to 23:40 on 13.10.2026, Vienna summer time: in UTC.
    expect(await sent?.clone().json()).toEqual({
      started_at: '2026-10-13T16:02:00.000Z',
      ended_at: '2026-10-13T21:40:00.000Z',
    });
  });

  it('switched off: the switch first, the hours only while some are recorded', async () => {
    const { calls } = mockFetch({
      ...base,
      '/api/v1/admin/attendance': {
        body: overview({ enabled: false, present: [], rows: overview().rows.slice(1) }),
      },
      'PUT /api/v1/admin/attendance/switch': { body: { enabled: true } },
    });
    renderPage(<Attendance />);

    const toggle = await screen.findByRole('switch', { name: 'Attendance on' });
    expect(screen.queryByRole('region', { name: 'Hours' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Here now' })).not.toBeInTheDocument();
    await userEvent.click(toggle);
    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PUT')).toBe(true);
    });
    const sent = calls.filter((call) => call.method === 'PUT').at(-1);
    expect(await sent?.clone().json()).toEqual({ enabled: true });
  });
});

describe('remote work, for whoever runs attendance', () => {
  const remote: Schemas['StayOut'] = {
    id: 6,
    started_at: '2026-10-14T16:00:00Z',
    ended_at: '2026-10-14T18:30:00Z',
    minutes: 150,
    state: 'done',
    in_place: null,
    out_place: null,
    corrected_by: null,
    corrected_at: null,
    remote: true,
    note: 'Wing rib CAD',
  };

  it('the switch, how much was remote, and an entry marked as not counted with a reason', async () => {
    const { calls } = mockFetch({
      ...base,
      '/api/v1/admin/attendance': {
        body: overview({
          remote_enabled: true,
          rows: [
            {
              user_id: 7,
              name: 'Anna Active',
              minutes: 205,
              remote_minutes: 150,
              stays: 3,
              not_checked_out: 0,
            },
          ],
        }),
      },
      '/api/v1/admin/attendance/people/7': { body: { user_id: 7, name: 'Anna Active', stays: [remote] } },
      'PUT /api/v1/admin/attendance/stays/6/not-counted': {
        body: { ...remote, not_counted_at: '2026-10-15T08:00:00Z', not_counted_reason: 'Logged twice' },
      },
    });
    renderPage(<Attendance />);

    expect(await screen.findByRole('switch', { name: 'Members may add remote work' })).toBeChecked();
    const hours = screen.getByRole('table', { name: 'Hours per person' });
    expect(within(hours).getByRole('row', { name: /Anna Active/ })).toHaveTextContent('2 h 30 min');

    await userEvent.click(screen.getByRole('button', { name: 'Anna Active' }));
    const person = await screen.findByRole('dialog', { name: 'Anna Active' });
    expect(await within(person).findByText('Wing rib CAD')).toBeInTheDocument();
    // Remote work is not changed by the leads, only marked or deleted.
    expect(within(person).queryByRole('button', { name: 'Change' })).not.toBeInTheDocument();
    await userEvent.click(within(person).getByRole('button', { name: 'Not counted' }));
    const form = await screen.findByRole('dialog', { name: 'Remote work of Anna Active' });
    const mark = within(form).getByRole('button', { name: 'Mark as not counted' });
    expect(mark).toBeDisabled();
    await userEvent.type(
      within(form).getByRole('textbox', { name: /Why does it not count/ }),
      'Logged twice',
    );
    await userEvent.click(mark);

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PUT')).toBe(true);
    });
    const sent = calls.filter((call) => call.method === 'PUT').at(-1);
    expect(await sent?.clone().json()).toEqual({ reason: 'Logged twice' });
  });
});

describe('periods', () => {
  it('this month and the last up to their ends; semesters from October and from March', () => {
    expect(periodOf('month', '2026-10-15')).toEqual({ start: '2026-10-01', end: '2026-10-15' });
    expect(periodOf('last', '2026-01-10')).toEqual({ start: '2025-12-01', end: '2025-12-31' });
    expect(periodOf('last', '2028-03-05')).toEqual({ start: '2028-02-01', end: '2028-02-29' });
    expect(periodOf('semester', '2026-10-15')).toEqual({ start: '2026-10-01', end: '2026-10-15' });
    expect(periodOf('semester', '2027-02-10')).toEqual({ start: '2026-10-01', end: '2027-02-10' });
    expect(periodOf('semester', '2027-05-10')).toEqual({ start: '2027-03-01', end: '2027-05-10' });
  });
});
