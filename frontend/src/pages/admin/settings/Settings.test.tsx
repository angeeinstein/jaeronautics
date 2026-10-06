import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { Billing } from './Billing';
import { Forum } from './Forum';
import { General } from './General';
import { Notifications } from './Notifications';

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/admin/settings';

function show(element: React.ReactNode, answers: Answers) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    ...answers,
  });
  renderPage(element);
  return fetched;
}

async function sentBody(calls: Request[], method: string, path: string): Promise<unknown> {
  await waitFor(() => {
    expect(calls.some((call) => call.method === method && new URL(call.url).pathname === path)).toBe(true);
  });
  return calls
    .filter((call) => call.method === method && new URL(call.url).pathname === path)
    .at(-1)
    ?.clone()
    .json();
}

const general: Schemas['GeneralOut'] = {
  invoice_payments: false,
  automatic_emails: true,
  legal_pdfs_in_welcome_emails: false,
  welcome_email_sender: 'office',
  automatic_email_template: 'welcome.html',
  institutional_email_domains: null,
  domains_in_use: ['edu.fh-joanneum.at', 'fh-joanneum.at'],
  senders: ['office', 'noreply'],
  templates: ['welcome.html'],
};

describe('general', () => {
  it('saves the section as it stands, and says so', async () => {
    const { calls } = show(<General />, {
      [`${API}/general`]: { body: general },
      [`PUT ${API}/general`]: { body: { changed: ['invoice_payments_enabled'] } },
    });

    await userEvent.click(await screen.findByRole('checkbox', { name: /Paying by invoice/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await sentBody(calls, 'PUT', `${API}/general`)).toEqual({
      invoice_payments: true,
      automatic_emails: true,
      legal_pdfs_in_welcome_emails: false,
      welcome_email_sender: 'office',
      automatic_email_template: 'welcome.html',
      institutional_email_domains: null,
    });
    expect(await screen.findByText('Saved.')).toBeInTheDocument();
    expect(screen.getByText('In use now: edu.fh-joanneum.at, fh-joanneum.at')).toBeInTheDocument();
  });

  it('nothing changed says so', async () => {
    show(<General />, {
      [`${API}/general`]: { body: general },
      [`PUT ${API}/general`]: { body: { changed: [] } },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Nothing changed.')).toBeInTheDocument();
  });

  it('a refusal is shown at its field', async () => {
    show(<General />, {
      [`${API}/general`]: { body: general },
      [`PUT ${API}/general`]: {
        status: 400,
        body: {
          error: {
            code: 'settings_invalid',
            message: 'That sender account does not exist.',
            fields: { welcome_email_sender: 'That sender account does not exist.' },
            details: null,
          },
        },
      },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Save' }));

    expect(await screen.findByText('That sender account does not exist.')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'From' })).toHaveAttribute('aria-invalid', 'true');
  });
});

describe('notifications', () => {
  it('each channel, how it stands', async () => {
    show(<Notifications />, {
      [`${API}/notifications`]: {
        body: {
          admin_general: true,
          admin_error: true,
          user_status: false,
          sender: null,
          senders: ['office'],
          health: [
            {
              channel: 'admin_general',
              label: 'Admin review queue',
              enabled: true,
              pending: 3,
              held_back: ['Picture submitted'],
              next_send_at: null,
              backoff_until: null,
              last_failure: null,
            },
          ],
        } satisfies Schemas['NotificationsOut'],
      },
    });

    const table = await screen.findByRole('table', { name: 'Notification channels' });
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent(
      'Admin review queueOn3Picture submittedNow–',
    );
    expect(
      screen.getByRole('checkbox', { name: 'Emails to members about their membership' }),
    ).not.toBeChecked();
  });
});

describe('membership fee', () => {
  const billing: Schemas['BillingOut'] = {
    publishable_key: 'pk_test_1',
    price_id: 'price_15',
    secret_key_set: true,
    webhook_secret_set: false,
  };

  it('of the secrets only whether one is set; empty keeps it', async () => {
    const { calls } = show(<Billing />, {
      [`${API}/billing`]: { body: billing },
      [`PUT ${API}/billing`]: { body: { changed: [], moving: 0 } },
    });

    expect(await screen.findByText('One is set. Leave empty to keep it.')).toBeInTheDocument();
    expect(screen.getByText('None set yet.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await sentBody(calls, 'PUT', `${API}/billing`)).toEqual({
      publishable_key: 'pk_test_1',
      price_id: 'price_15',
      secret_key: null,
      webhook_secret: null,
    });
  });

  it('a new price asks again, then says how many move', async () => {
    const { calls } = show(<Billing />, {
      [`${API}/billing`]: { body: billing },
      [`PUT ${API}/billing`]: { body: { changed: ['stripe_price_id'], moving: 12 } },
    });

    const price = await screen.findByRole('textbox', { name: /Membership price/ });
    await userEvent.clear(price);
    await userEvent.type(price, 'price_20');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(calls.some((call) => call.method === 'PUT')).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Yes, change the price' }));

    expect(await screen.findByText(/12 running membership\(s\) move to the new price/)).toBeInTheDocument();
  });
});

describe('forum', () => {
  const forum: Schemas['ForumSettingsOut'] = {
    enabled: true,
    base_url: 'https://forum.example.org',
    api_username: 'system',
    api_key_set: true,
    connect_secret_set: true,
    onboarding_group: 'members-onboarding',
    member_group: 'members',
    inactive_group: 'membership-inactive',
    staff_group: '',
    manage_staff_flags: false,
    category_groups: [
      { kind: 'student', label: 'Student', group: 'students' },
      { kind: 'staff', label: 'Staff', group: '' },
    ],
    lecture_groups: 'students',
    archive_groups: '',
    onboarding_path: '/',
    avatar_max_bytes: 5242880,
    avatar_allowed_types: ['jpg', 'png'],
    endpoints: {
      public_base_url: 'https://members.example.org',
      entry: 'https://members.example.org/forum',
      connect: 'https://members.example.org/forum/connect',
      logout: 'https://members.example.org/forum/logout',
    },
    missing: [],
  };

  it('a group per kind of member, sent as such', async () => {
    const { calls } = show(<Forum />, {
      [`${API}/forum`]: { body: forum },
      [`PUT ${API}/forum`]: { body: { changed: ['forum_category_groups'] } },
    });

    await userEvent.type(await screen.findByRole('textbox', { name: 'Staff' }), 'lecturers');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    const body = (await sentBody(calls, 'PUT', `${API}/forum`)) as Schemas['ForumIn'];
    expect(body.category_groups).toEqual({ student: 'students', staff: 'lecturers' });
    expect(body.api_key).toBeNull();
    expect(body.avatar_allowed_types).toEqual(['jpg', 'png']);
  });

  it('the connection test says how it went', async () => {
    show(<Forum />, {
      [`${API}/forum`]: { body: forum },
      [`POST ${API}/forum/test`]: { body: { ok: false, message: 'The forum did not answer.' } },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Test the connection' }));

    expect(await screen.findByRole('status')).toHaveTextContent('The forum did not answer.');
  });
});
