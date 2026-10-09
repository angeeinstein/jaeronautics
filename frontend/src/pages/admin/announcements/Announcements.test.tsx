import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { mockFetch, renderPage } from '../../../test/render';
import { Messages } from '../Messages';
import { Announcements, assemblyMoment, invitationText } from './Announcements';

afterEach(() => {
  vi.unstubAllGlobals();
});

const base = {
  '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
  '/api/v1/me': { body: makeMe() },
};

const sentOne: Schemas['MailingOut'] = {
  id: 7,
  kind: 'news',
  kind_label: 'News',
  audience_label: 'All active members',
  subject: 'Summer party',
  body: 'Friday',
  assembly_at: null,
  author: 'Ada Admin',
  created_at: '2026-10-01T10:00:00Z',
  status: 'sending',
  finished_at: null,
  recipient_count: 40,
  unsubscribed_count: 2,
  sent: 10,
  failed: 1,
  waiting: 29,
  stopped: 0,
};

const page: Schemas['AnnouncementsOut'] = {
  items: [sentOne],
  kinds: [
    { value: 'student', label: 'Student' },
    { value: 'alumni', label: 'Alumni' },
  ],
  teams: [{ id: 3, name: 'Rocket Team' }],
  limits: { per_hour: 100, per_day: 300, sender: 'office' },
};

describe('announcements', () => {
  it('what was sent, how far each is, and the limits', async () => {
    mockFetch({
      ...base,
      'GET /api/v1/admin/announcements': { body: page },
      'POST /api/v1/admin/announcements/count': { body: { recipients: 38, unsubscribed: 2 } },
    });
    renderPage(<Announcements />);
    expect(await screen.findByRole('link', { name: 'Summer party' })).toHaveAttribute(
      'href',
      '/admin/announcements/7',
    );
    expect(screen.getByText(/10 of 40 sent, 1 failed, 29 waiting/)).toBeInTheDocument();
    expect(screen.getByText(/at most 100 an hour and 300 a day/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Send to 38 people' })).toBeDisabled();
    expect(screen.getByText('2 switched the news off and are left out.')).toBeInTheDocument();
  });

  it('a news email: a test first, then sent to everybody counted', async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch({
      ...base,
      'GET /api/v1/admin/announcements': { body: { ...page, items: [] } },
      'POST /api/v1/admin/announcements/count': { body: { recipients: 38, unsubscribed: 0 } },
      'POST /api/v1/admin/announcements/test': { body: { to: 'admin@example.org' } },
      'POST /api/v1/admin/announcements': { status: 201, body: { ...sentOne, id: 9 } },
      'GET /api/v1/admin/announcements/9': { body: { ...sentOne, id: 9, failed_addresses: [] } },
    });
    renderPage(<Announcements />);
    await user.type(await screen.findByLabelText(/^Subject/), 'Hello');
    await user.type(screen.getByLabelText(/^Text/), 'News of the week');
    await user.click(screen.getByRole('button', { name: 'Send a test to me' }));
    await waitFor(() => {
      expect(calls.some((call) => call.url.endsWith('/announcements/test'))).toBe(true);
    });
    await user.click(await screen.findByRole('button', { name: 'Send to 38 people' }));
    await user.click(await screen.findByRole('button', { name: 'Yes, send to 38 people' }));
    await waitFor(() => {
      expect(
        calls.some((call) => call.method === 'POST' && call.url.endsWith('/api/v1/admin/announcements')),
      ).toBe(true);
    });
    const post = calls.find(
      (call) => call.method === 'POST' && call.url.endsWith('/api/v1/admin/announcements'),
    );
    expect(await post?.clone().json()).toMatchObject({
      kind: 'news',
      audience: { scope: 'all' },
      subject: 'Hello',
      body: 'News of the week',
    });
  });

  it('the general assembly: the invitation written from its facts, held back under two weeks', async () => {
    const user = userEvent.setup();
    mockFetch({
      ...base,
      'GET /api/v1/admin/announcements': { body: { ...page, items: [] } },
      'POST /api/v1/admin/announcements/count': { body: { recipients: 38, unsubscribed: 0 } },
    });
    renderPage(<Announcements />);
    await user.click(await screen.findByRole('radio', { name: 'General assembly' }));
    expect(screen.getByRole('button', { name: 'Write the invitation' })).toBeDisabled();
    expect(screen.getByText('Give the date and time.')).toBeInTheDocument();
  });
});

describe('the invitation', () => {
  it('has date, time, place and the agenda numbered', () => {
    const { subject, body } = invitationText(
      '2026-11-20',
      '18:30',
      'FH Joanneum, AP149.238',
      '- Report\n2. Election',
    );
    expect(subject).toBe('Invitation to the general assembly on 20.11.2026');
    expect(body).toContain('**When:** 20.11.2026, 18:30');
    expect(body).toContain('**Where:** FH Joanneum, AP149.238');
    expect(body).toContain('1. Report\n2. Election');
    expect(body).toContain('§ 10 (4)');
  });

  it('needs a day and a time', () => {
    expect(assemblyMoment('', '18:00')).toBeNull();
    expect(assemblyMoment('2026-11-20', 'soon')).toBeNull();
    expect(assemblyMoment('2026-11-20', '18:00')?.getHours()).toBe(18);
  });
});

describe('Admin › Messages', () => {
  const message: Schemas['ContactMessageOut'] = {
    id: 4,
    topic: 'membership',
    topic_label: 'Membership & payment',
    sender_name: 'Vera Visitor',
    sender_email: 'vera@example.net',
    account_id: 12,
    membership: 'active',
    page: '/account',
    subject: 'My fee',
    body: 'Was it paid?\nThanks',
    created_at: '2026-10-09T08:00:00Z',
    delivered: true,
    done_at: null,
    done_by: null,
  };

  it('answered by email, linked to the account, marked done', async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch({
      ...base,
      'GET /api/v1/admin/messages': { body: { items: [message], open_count: 1 } },
      'PUT /api/v1/admin/messages/4': {
        body: { ...message, done_at: '2026-10-09T09:00:00Z', done_by: 'Ada Admin' },
      },
    });
    renderPage(<Messages />);
    expect(await screen.findByText('My fee')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Answer by email' })).toHaveAttribute(
      'href',
      'mailto:vera@example.net?subject=Re%3A%20My%20fee',
    );
    expect(screen.getByRole('link', { name: 'Account no. 12, active' })).toHaveAttribute(
      'href',
      '/admin/accounts/12',
    );
    expect(screen.getByRole('radio', { name: 'Open (1)' })).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Mark done' }));
    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PUT')).toBe(true);
    });
  });
});
