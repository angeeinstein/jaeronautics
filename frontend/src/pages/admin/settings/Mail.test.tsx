import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { MailAccountsTab as MailAccounts } from './MailAccounts';
import { TestEmailTab as TestEmail } from './TestEmail';

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/admin/settings/mail';

const office: Schemas['MailAccountOut'] = {
  id: 4,
  key: 'office',
  kind: 'smtp',
  host: 'smtp.example.org',
  port: 587,
  username: 'office@example.org',
  starttls: true,
  from_email: 'noreply@example.org',
  from_name: 'Joanneum Aeronautics',
};

const notConnected: Schemas['MicrosoftOut'] = {
  tenant_id: null,
  client_id: null,
  certificate: null,
  new_certificate: null,
  ready: false,
};

function listed(accounts: Schemas['MailAccountOut'][], microsoft = notConnected): Schemas['MailOut'] {
  return { accounts, microsoft };
}

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
    show(<MailAccounts />, { [API]: { body: listed([office]) } });

    const table = await screen.findByRole('table', { name: 'Mail accounts' });
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent(
      'officesmtp.example.org:587STARTTLSoffice@example.orgsends as Joanneum Aeronautics <noreply@example.org>',
    );
  });

  it('the browser leaves its saved login out of the SMTP login', async () => {
    show(<MailAccounts />, { [API]: { body: listed([]) } });

    const username = await screen.findByLabelText('Username');
    const password = screen.getByLabelText('Password');
    // Read-only until focused: browsers fill only what they could type into.
    expect(username).toHaveAttribute('readonly');
    expect(username).toHaveAttribute('autocomplete', 'off');
    expect(password).toHaveAttribute('autocomplete', 'new-password');
    expect(password).toHaveAttribute('data-1p-ignore');

    await userEvent.type(username, 'smtp-user');
    expect(username).not.toHaveAttribute('readonly');
    expect(username).toHaveValue('smtp-user');
  });

  it('a change keeps the password unless one is typed', async () => {
    const { calls } = show(<MailAccounts />, {
      [API]: { body: listed([office]) },
      [`PUT ${API}/accounts/4`]: { body: { ...office, host: 'smtp2.example.org' } },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Change' }));
    const form = screen.getByRole('region', { name: 'Change office' });
    const host = within(form).getByRole('textbox', { name: 'SMTP server' });
    await userEvent.clear(host);
    await userEvent.type(host, 'smtp2.example.org');
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }));

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
      [API]: { body: listed([]) },
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
      [API]: { body: listed([office]) },
      [`DELETE ${API}/accounts/4`]: { body: { cleared_senders: ['welcome'] } },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Remove' }));
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Yes, remove' }));

    expect(await screen.findByText(/goes out from the default account now/)).toBeInTheDocument();
  });

  it('the export needs the current password, and a wrong one is said there', async () => {
    show(<MailAccounts />, {
      [API]: { body: listed([office]) },
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

describe('Microsoft 365 accounts', () => {
  const support: Schemas['MailAccountOut'] = {
    id: 7,
    key: 'support',
    kind: 'microsoft',
    host: null,
    port: null,
    username: null,
    starttls: false,
    from_email: 'support@example.org',
    from_name: 'Joanneum Aeronautics',
  };
  const connected: Schemas['MicrosoftOut'] = {
    tenant_id: 'example.onmicrosoft.com',
    client_id: '11111111-2222-3333-4444-555555555555',
    certificate: {
      thumbprint: 'AB12CD34EF56AB12CD34EF56AB12CD34EF56AB12',
      expires_at: '2029-10-16T20:00:00Z',
    },
    new_certificate: null,
    ready: true,
  };

  it('beside the SMTP ones, as their mailbox', async () => {
    show(<MailAccounts />, { [API]: { body: listed([office, support], connected) } });

    const table = await screen.findByRole('table', { name: 'Mail accounts' });
    expect(within(table).getAllByRole('row')[2]).toHaveTextContent(
      'supportMicrosoft 365Graphsupport@example.orgas Joanneum Aeronautics',
    );
    expect(screen.getByText('AB12CD34EF56AB12CD34EF56AB12CD34EF56AB12')).toBeInTheDocument();
  });

  it('one is added with its mailbox alone', async () => {
    const { calls } = show(<MailAccounts />, {
      [API]: { body: listed([], connected) },
      [`POST ${API}/accounts`]: { status: 201, body: support },
    });

    await userEvent.click(await screen.findByRole('radio', { name: 'Microsoft 365' }));
    expect(screen.queryByRole('textbox', { name: 'SMTP server' })).not.toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox', { name: 'Key' }), 'support');
    await userEvent.type(screen.getByRole('textbox', { name: 'Mailbox' }), 'support@example.org');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(async () => {
      expect(await lastBody(calls, 'POST', `${API}/accounts`)).toMatchObject({
        key: 'support',
        kind: 'microsoft',
        from_email: 'support@example.org',
      });
    });
  });

  it('a certificate is made here, and a sign-in checked', async () => {
    const { calls } = show(<MailAccounts />, {
      [API]: { body: listed([], { ...connected, certificate: null, ready: false }) },
      [`POST ${API}/microsoft/certificate`]: { status: 201, body: connected },
      [`POST ${API}/microsoft/check`]: {
        body: { ok: true, message: 'Signed in to Microsoft.', microsoft: connected },
      },
    });

    expect(await screen.findByRole('button', { name: 'Check sign-in' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Make a certificate' }));
    await waitFor(() => {
      expect(
        calls.some((call) => call.method === 'POST' && call.url.endsWith('/microsoft/certificate')),
      ).toBe(true);
    });
  });
});
