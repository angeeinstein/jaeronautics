import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { MailAccounts } from './MailAccounts';
import { TestEmail } from './TestEmail';

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/admin/settings/mail';

const office: Schemas['MailAccountOut'] = {
  id: 4,
  key: 'office',
  host: 'smtp.example.org',
  port: 587,
  username: 'office@example.org',
  starttls: true,
  from_email: 'noreply@example.org',
  from_name: 'Joanneum Aeronautics',
};

function show(element: React.ReactNode, answers: Answers) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe({ email: 'anna@example.org' }) },
    ...answers,
  });
  renderPage(element);
  return fetched;
}

function lastBody(calls: Request[], method: string, path: string) {
  return calls
    .filter((call) => call.method === method && new URL(call.url).pathname === path)
    .at(-1)
    ?.clone()
    .json();
}

describe('mail accounts', () => {
  it('each account, as it sends', async () => {
    show(<MailAccounts />, { [API]: { body: { accounts: [office] } } });

    const table = await screen.findByRole('table', { name: 'Mail accounts' });
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent(
      'officesmtp.example.org:587STARTTLSoffice@example.orgsends as Joanneum Aeronautics <noreply@example.org>',
    );
  });

  it('a change keeps the password unless one is typed', async () => {
    const { calls } = show(<MailAccounts />, {
      [API]: { body: { accounts: [office] } },
      [`PUT ${API}/accounts/4`]: { body: { ...office, host: 'smtp2.example.org' } },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Change' }));
    expect(screen.getByRole('region', { name: 'Change office' })).toBeInTheDocument();
    const host = screen.getByRole('textbox', { name: 'SMTP server' });
    await userEvent.clear(host);
    await userEvent.type(host, 'smtp2.example.org');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(async () => {
      expect(await lastBody(calls, 'PUT', `${API}/accounts/4`)).toMatchObject({
        key: 'office',
        host: 'smtp2.example.org',
        password: null,
      });
    });
  });

  it('a refusal is shown at its field', async () => {
    show(<MailAccounts />, {
      [API]: { body: { accounts: [] } },
      [`POST ${API}/accounts`]: {
        status: 409,
        body: {
          error: {
            code: 'mail_account_key_taken',
            message: 'A mail account with this key already exists.',
            fields: { key: 'A mail account with this key already exists.' },
            details: null,
          },
        },
      },
    });

    expect(await screen.findByText('None yet: no email can be sent until there is one.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));

    expect(await screen.findByText('A mail account with this key already exists.')).toBeInTheDocument();
  });

  it('removing asks again', async () => {
    const { calls } = show(<MailAccounts />, {
      [API]: { body: { accounts: [office] } },
      [`DELETE ${API}/accounts/4`]: { body: { removed_welcome_sender: true } },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Remove' }));
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Yes, remove' }));

    expect(await screen.findByText(/It sent the welcome email/)).toBeInTheDocument();
  });

  it('the export needs the current password, and a wrong one is said there', async () => {
    show(<MailAccounts />, {
      [API]: { body: { accounts: [office] } },
      [`POST ${API}/export`]: {
        status: 403,
        body: {
          error: {
            code: 'password_wrong',
            message: 'Your current password is needed.',
            fields: { password: 'That is not your current password.' },
            details: null,
          },
        },
      },
    });

    const exportButton = await screen.findByRole('button', { name: 'Export' });
    expect(exportButton).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Your current password'), 'wrong');
    await userEvent.click(exportButton);

    expect(await screen.findByText('That is not your current password.')).toBeInTheDocument();
  });
});

describe('the test email', () => {
  it('to the person sending it, unless changed', async () => {
    const { calls } = show(<TestEmail />, {
      '/api/v1/admin/settings/test-email': {
        body: { senders: ['office'], templates: ['welcome_email.html', 'test_email.html'] },
      },
      'POST /api/v1/admin/settings/test-email': { body: { ok: true } },
    });

    const to = await screen.findByRole('textbox', { name: 'To' });
    await waitFor(() => {
      expect(to).toHaveValue('anna@example.org');
    });
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByText('Sent to anna@example.org.')).toBeInTheDocument();
    expect(await lastBody(calls, 'POST', '/api/v1/admin/settings/test-email')).toEqual({
      sender: 'office',
      recipient: 'anna@example.org',
      template: 'welcome_email.html',
    });
  });

  it('nothing to send from says where to add one', async () => {
    show(<TestEmail />, {
      '/api/v1/admin/settings/test-email': { body: { senders: [], templates: ['welcome_email.html'] } },
    });

    expect(await screen.findByText(/Add one under Mail accounts/)).toBeInTheDocument();
  });
});
