import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { Account } from './Account';

type AccountOut = Schemas['AccountOut'];

afterEach(() => {
  vi.unstubAllGlobals();
});

const ACCOUNT = '/api/v1/admin/accounts/7';

function account(overrides: Partial<AccountOut> = {}): AccountOut {
  return {
    id: 7,
    email: 'anna@example.org',
    email_verified: true,
    name: 'Anna Berger',
    forum_username: 'BergerA_L25',
    roles: [],
    account_state: 'active',
    disabled_at: null,
    disabled_reason: null,
    erased_at: null,
    membership: {
      title: null,
      first_name: 'Anna',
      last_name: 'Berger',
      email_private: 'anna@example.org',
      category: 'student',
      category_label: 'Student',
      year_group: 'LAV25',
      state: 'active',
      payment_status: 'paid',
      payment_status_label: 'Paid',
      is_active: true,
      ends_on: '2026-12-31',
      renews_on: '2027-01-01',
      has_billing: true,
      picture_replacement_allowed_since: null,
    },
    old_forum: null,
    forum: {
      account_state: 'active',
      status: 'active',
      status_label: 'Has forum access',
      latest_picture: 'approved',
      review_note: null,
      last_synced_at: '2026-10-01T10:00:00Z',
      error: null,
      cleanup: null,
      has_picture: true,
    },
    teams: { roles: [], memberships: [] },
    access: {
      options: [
        {
          slug: 'admin',
          label: 'Admin',
          description: 'Administers members.',
          held: false,
          covered_by: 'Super Admin',
          permissions: ['Open the admin workspace'],
        },
        {
          slug: 'superadmin',
          label: 'Super Admin',
          description: 'Installs updates.',
          held: true,
          covered_by: null,
          permissions: ['Install a new version and roll one back'],
        },
      ],
      effective_permissions: ['Install a new version and roll one back', 'Open the admin workspace'],
      roles_locked: null,
      removal_warning: null,
      disable_blockers: [],
    },
    recent_activity: [],
    actions: {
      sync_billing: true,
      resync_forum: true,
      picture_replacement: true,
      correct_email: true,
      reconnect: true,
      manage_access: true,
      export_data: true,
      erase: true,
    },
    ...overrides,
  };
}

function show(
  data: AccountOut = account(),
  { hash = '', answers = {} }: { hash?: string; answers?: Answers } = {},
) {
  const fetched = mockFetch({
    [ACCOUNT]: { body: data },
    '/api/v1/me': { body: makeMe() },
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    [`${ACCOUNT}/old-forum-candidates`]: { body: { items: [], likely: true } },
    ...answers,
  });
  renderPage(<Account />, { route: `/admin/accounts/7${hash}`, path: '/admin/accounts/:userId' });
  return fetched;
}

/** The body the latest change sent to ``path``. */
async function sent(calls: Request[], method: string, path: string): Promise<unknown> {
  const request = calls
    .filter((call) => call.method === method && new URL(call.url).pathname === path)
    .at(-1);
  expect(request, `${method} ${path}`).toBeDefined();
  return request ? request.clone().json() : undefined;
}

describe('the head of the page', () => {
  it('names the account, its state and its address, under the breadcrumb', async () => {
    show();

    expect(await screen.findByRole('heading', { name: 'Anna Berger', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toHaveTextContent(
      'Admin›Accounts›Anna Berger',
    );
    expect(screen.getAllByText('anna@example.org').length).toBeGreaterThan(0);
  });

  it('shows what stops an account before the membership', async () => {
    show(account({ account_state: 'disabled', disabled_at: '2026-09-01T10:00:00Z' }));

    const [inHead] = await screen.findAllByText('Deactivated', { selector: '.mantine-Badge-label' });
    // The first one is above the tabs, beside the name.
    expect(inHead?.compareDocumentPosition(screen.getByRole('tablist'))).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it('offers only the tabs the person looking may use', async () => {
    show(
      account({
        teams: null,
        access: null,
        actions: { ...account().actions, manage_access: false, erase: false },
      }),
    );

    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      'Profile',
      'Membership',
      'Forum',
      'Roles',
      'Activity',
    ]);
  });

  it('opens the tab named in the address', async () => {
    show(account(), { hash: '#membership' });

    expect(await screen.findByRole('tab', { name: 'Membership', selected: true })).toBeInTheDocument();
    expect(screen.getByText('31.12.2026')).toBeInTheDocument();
  });

  it('an account that does not exist', async () => {
    show(account(), {
      answers: {
        [ACCOUNT]: {
          status: 404,
          body: {
            error: { code: 'not_found', message: 'There is no such account.', fields: null, details: null },
          },
        },
      },
    });

    expect(await screen.findByRole('alert')).toHaveTextContent('There is no such account.');
  });
});

describe('profile', () => {
  it('corrects the private address after a second click', async () => {
    const { calls } = show(account(), {
      answers: { [`PUT ${ACCOUNT}/email`]: { body: { email: 'anna.right@example.org' } } },
    });

    await userEvent.type(await screen.findByLabelText('New private address'), 'anna.right@example.org');
    await userEvent.click(screen.getByRole('button', { name: 'Change address' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, change it' }));

    expect(await sent(calls, 'PUT', `${ACCOUNT}/email`)).toEqual({ email: 'anna.right@example.org' });
  });

  it('puts a refused address beside the field', async () => {
    show(account(), {
      answers: {
        [`PUT ${ACCOUNT}/email`]: {
          status: 400,
          body: {
            error: {
              code: 'validation_error',
              message: 'Enter a valid email address.',
              fields: { email: 'Enter a valid email address.' },
              details: null,
            },
          },
        },
      },
    });

    await userEvent.type(await screen.findByLabelText('New private address'), 'nonsense');
    await userEvent.click(screen.getByRole('button', { name: 'Change address' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, change it' }));

    expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument();
  });

  it('not offered without the right to', async () => {
    show(account({ actions: { ...account().actions, correct_email: false } }));

    await screen.findByRole('heading', { name: 'Anna Berger', level: 1 });
    expect(screen.queryByLabelText('New private address')).toBeNull();
  });
});

describe('roles', () => {
  it('say which role covers which, and that permissions combine', async () => {
    show(account(), { hash: '#roles' });

    expect(await screen.findByText('included in Super Admin')).toBeInTheDocument();
    expect(screen.getByText(/combined permissions of every role chosen/)).toBeInTheDocument();
  });

  it('keep what an account and a role can do folded until asked', async () => {
    show(account(), { hash: '#roles' });

    const can = await screen.findByRole('button', { name: /What this account can do now \(2\)/ });
    expect(screen.queryByText('Open the admin workspace')).toBeNull();

    await userEvent.click(can);

    expect(screen.getByText('Open the admin workspace')).toBeInTheDocument();
  });

  it('save the whole set after a second click', async () => {
    const { calls } = show(account(), {
      hash: '#roles',
      answers: { [`PUT ${ACCOUNT}/roles`]: { body: { changed: true, redundant: [] } } },
    });

    await userEvent.click(await screen.findByRole('checkbox', { name: /^Admin/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Save roles' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, change the roles' }));

    expect(await sent(calls, 'PUT', `${ACCOUNT}/roles`)).toEqual({ roles: ['superadmin', 'admin'] });
  });

  it('nothing to save until something changes', async () => {
    show(account(), { hash: '#roles' });

    expect(await screen.findByRole('button', { name: 'Save roles' })).toBeDisabled();
  });

  it('one’s own cannot be changed, and the page says so', async () => {
    show(
      account({
        access: { ...(account().access ?? never()), roles_locked: 'You cannot change your own roles.' },
      }),
      {
        hash: '#roles',
      },
    );

    expect(await screen.findByText('You cannot change your own roles.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save roles' })).toBeNull();
  });
});

describe('the danger zone', () => {
  const erasure: Schemas['ErasureOut'] = {
    blockers: [],
    confirm_email: 'anna@example.org',
    paid_periods: 2,
    has_forum_account: true,
    has_stripe_customer: true,
    subscription_active: true,
    subscription_status: 'active',
    coverage_end: '2026-12-31',
  };

  it('deactivates with a reason, after a second click', async () => {
    const { calls } = show(account(), {
      hash: '#danger',
      answers: {
        [`${ACCOUNT}/erasure`]: { body: erasure },
        [`PUT ${ACCOUNT}/disabled`]: { body: { changed: true } },
      },
    });

    await userEvent.type(await screen.findByLabelText('Reason'), 'Conduct');
    await userEvent.click(screen.getByRole('button', { name: 'Deactivate' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, deactivate' }));

    expect(await sent(calls, 'PUT', `${ACCOUNT}/disabled`)).toEqual({ disabled: true, reason: 'Conduct' });
  });

  it('says why an account cannot be switched off', async () => {
    show(
      account({
        access: {
          ...(account().access ?? never()),
          disable_blockers: ['This is the only account that can do this.'],
        },
      }),
      { hash: '#danger', answers: { [`${ACCOUNT}/erasure`]: { body: erasure } } },
    );

    expect(await screen.findByText('This is the only account that can do this.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Deactivate' })).toBeNull();
  });

  it('says what erasing does, and erases only once the address is typed', async () => {
    const { calls } = show(account(), {
      hash: '#danger',
      answers: {
        [`${ACCOUNT}/erasure`]: { body: erasure },
        [`POST ${ACCOUNT}/erase`]: { body: { subscription_cancelled: true, forum_deferred: false } },
      },
    });

    expect(await screen.findByText(/2 paid periods stay/)).toBeInTheDocument();
    expect(screen.getByText(/cancelled at once, without a refund/)).toBeInTheDocument();
    const erase = screen.getByRole('button', { name: 'Erase personal data' });
    expect(erase).toBeDisabled();

    await userEvent.type(screen.getByLabelText('Type anna@example.org to confirm'), 'Anna@Example.org');
    await userEvent.click(erase);

    expect(await sent(calls, 'POST', `${ACCOUNT}/erase`)).toEqual({
      confirm_email: 'Anna@Example.org',
      reason: null,
    });
  });

  it('names what forbids erasing', async () => {
    show(account(), {
      hash: '#danger',
      answers: {
        [`${ACCOUNT}/erasure`]: {
          body: { ...erasure, blockers: ['You cannot erase your own account here.'] },
        },
      },
    });

    expect(await screen.findByText('You cannot erase your own account here.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Erase personal data' })).toBeNull();
  });
});

describe('the forum', () => {
  it('offers the likely old accounts and reconnects one, then goes to its new home', async () => {
    const { calls } = show(account(), {
      hash: '#forum',
      answers: {
        [`${ACCOUNT}/old-forum-candidates`]: {
          body: {
            likely: true,
            items: [
              {
                id: 3,
                username: 'BergerA_L22',
                display_name: 'Anna Berger',
                year_group: 'LAV22',
                email: 'anna.berger@edu.example',
                posts: 12,
                likely_because: 'address',
              },
            ],
          },
        },
        [`POST ${ACCOUNT}/reconnect`]: {
          body: { account_id: 99, old_username: 'BergerA_L22', forum: 'synced', forum_error: null },
        },
        '/api/v1/admin/accounts/99': { body: account({ id: 99 }) },
      },
    });

    const table = await screen.findByRole('table', { name: 'Old forum accounts' });
    expect(within(table).getByText('Differs only in dots or spelling')).toBeInTheDocument();
    await userEvent.click(within(table).getByRole('button', { name: 'Reconnect' }));
    await userEvent.click(within(table).getByRole('button', { name: 'Yes, reconnect' }));

    expect(await sent(calls, 'POST', `${ACCOUNT}/reconnect`)).toEqual({ profile_id: 3 });
    await waitFor(() => {
      expect(calls.some((call) => new URL(call.url).pathname === '/api/v1/admin/accounts/99')).toBe(true);
    });
  });

  it('a leftover that could not be removed is said plainly', async () => {
    show(account({ forum: { ...account().forum, cleanup: { failed: true, error: 'Discourse said no.' } } }), {
      hash: '#forum',
    });

    expect(await screen.findByText(/could not be removed.*Discourse said no\./)).toBeInTheDocument();
  });
});

it('without a membership, says why there is none', async () => {
  show(
    account({
      membership: null,
      old_forum: {
        display_name: 'Alen Popovic',
        username: 'PopovicA_L23',
        year_group: 'LAV23',
        email: null,
        group: null,
        group_reason: null,
        posts: 7,
        joined_on: null,
        last_posted_on: null,
        claimed_at: null,
        picture_url: null,
      },
    }),
    { hash: '#membership' },
  );

  expect(await screen.findByText('Not recorded: joined before the portal existed.')).toBeInTheDocument();
});

function never(): never {
  throw new Error('fixture without access');
}
