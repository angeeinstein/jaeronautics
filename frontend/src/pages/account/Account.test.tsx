import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { makeMe } from '../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { Account, passed } from './Account';
import { MembershipPage } from './MembershipPage';
import { Profile } from './Profile';

afterEach(() => {
  vi.unstubAllGlobals();
});

const options: Schemas['FormOptionsOut'] = {
  countries: [
    { value: 'Austria', label: 'Austria' },
    { value: 'Germany', label: 'Germany' },
  ],
  salutations: [
    { value: 'Mr', label: 'Mr' },
    { value: 'Ms', label: 'Ms' },
  ],
  member_categories: [
    {
      value: 'student',
      label: 'Student',
      description: 'Currently studying.',
      year_group: 'required',
      university_email_required: true,
      company_name: false,
      joinable: true,
    },
    {
      value: 'partner',
      label: 'Company or partner',
      description: 'Joined on behalf of a company.',
      year_group: 'hidden',
      university_email_required: false,
      company_name: true,
      joinable: true,
    },
  ],
  invoice_payments: false,
  university_domains: ['edu.fh-joanneum.at', 'fh-joanneum.at'],
};

const member: Schemas['MemberAccountOut'] = {
  work_email: { address: 'anna@edu.fh-joanneum.at', confirmed: false },
  membership: {
    status: 'canceled',
    status_label: 'Ended',
    tone: 'neutral',
    active: false,
    starts_on: '2025-01-01',
    ends_on: '2025-12-31',
    renews_on: null,
    auto_renew: false,
    activating: false,
    note: { tone: 'plain', text: 'Your membership has ended. You can rejoin at any time.' },
    may_manage_billing: true,
    may_resume_payment: false,
    may_rejoin: true,
    invoice_payments: true,
  },
  forum: {
    status: 'inactive_membership',
    message: 'Forum access needs an active membership.',
    username: 'BergerA_L25',
    reconnect_waiting: false,
    may_open: false,
    open_url: '/forum',
    problem: false,
    picture: null,
  },
  teams: null,
  contact: {
    street: 'Main',
    house_number: '1',
    postal_code: '8010',
    city: 'Graz',
    country: 'Austria',
    phone_private: '+43123',
    email_private: 'anna@example.org',
    phone_work: null,
    email_work: 'anna@edu.fh-joanneum.at',
    company_name: null,
  },
  identity: {
    salutation: 'Ms',
    title: null,
    first_name: 'Anna',
    last_name: 'Berger',
    member_category: 'student',
    member_category_label: 'Student',
    year_group: 'LAV25',
  },
  change_request: null,
  deletion_note: null,
  may_cancel_instead: false,
};

const account: Schemas['MyAccountOut'] = {
  email: { address: 'anna@example.org', confirmed: true },
  to_confirm: {
    tone: 'warning',
    text: 'Please confirm your university email address. We sent a link to it.',
  },
  member,
  export_url: '/account/data-export',
};

const PAGES = {
  '/account': <Account />,
  '/account/profile': <Profile />,
  '/account/membership': <MembershipPage />,
} as const;

function show(answers: Answers = {}, route: keyof typeof PAGES = '/account') {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    '/api/v1/account': { body: account },
    '/api/v1/forms/options': { body: options },
    ...answers,
  });
  renderPage(PAGES[route], { route, path: route });
  return fetched;
}

async function sentBody(calls: Request[], method: string, path: string) {
  const sent = calls.find((call) => call.method === method && new URL(call.url).pathname === path);
  return sent ? ((await sent.clone().json()) as unknown) : undefined;
}

/** Profile, with the contact details opened for changing. */
async function editContact(answers: Answers = {}) {
  const fetched = show(answers, '/account/profile');
  const contact = await screen.findByRole('region', { name: 'Contact details' });
  await userEvent.click(within(contact).getByRole('button', { name: 'Edit' }));
  return { ...fetched, contact };
}

describe('my account: the overview', () => {
  it('first what is to be done, sent again from right there', async () => {
    const { calls } = show({
      'POST /api/v1/account/emails/work/confirmation': { body: { tone: 'success', text: 'Sent.' } },
    });

    expect(await screen.findByRole('heading', { level: 1, name: 'Anna Berger' })).toBeInTheDocument();
    const todo = screen.getByRole('region', { name: 'To do' });
    expect(within(todo).getByText(/Please confirm your university email address/)).toBeInTheDocument();
    // Only the university address waits: one button, for it.
    await userEvent.click(within(todo).getByRole('button', { name: 'Send the link again' }));

    await waitFor(() => {
      expect(
        calls.some((call) => new URL(call.url).pathname === '/api/v1/account/emails/work/confirmation'),
      ).toBe(true);
    });
  });

  it('each part a tile, the way to its page', async () => {
    show();

    const membership = await screen.findByRole('link', { name: 'Membership' });
    expect(membership).toHaveAttribute('href', '/account/membership');
    expect(membership).toHaveAccessibleDescription(/Ended.*31\.12\.2025.*was paid until/);
    // An ended membership does not say when it would renew.
    expect(membership).not.toHaveAccessibleDescription(/renew/i);
    const forum = screen.getByRole('link', { name: 'Forum and picture' });
    expect(forum).toHaveAttribute('href', '/account/forum');
    expect(forum).toHaveAccessibleDescription(/BergerA_L25/);
    // The details are on the pages in the side menu, and the password in the menu at the top right.
    expect(screen.queryByRole('region', { name: 'Contact details' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Change password' })).not.toBeInTheDocument();
  });

  it('teams in a tile of their own, when there are any to show', async () => {
    show({
      '/api/v1/account': {
        body: {
          ...account,
          member: {
            ...member,
            teams: {
              plural: 'Teams',
              invite: false,
              mine: [
                { slug: 'rocket', name: 'Rocket Team', logo_url: null, status_label: 'Lead', opens: 'team' },
              ],
            },
          },
        },
      },
    });

    const teams = await screen.findByRole('link', { name: 'Teams' });
    expect(teams).toHaveAttribute('href', '/teams');
    expect(teams).toHaveAccessibleDescription(/Rocket Team.*Lead/);
  });

  it('an account without a membership has the way to start one', async () => {
    show({
      '/api/v1/account': { body: { ...account, to_confirm: null, member: null } },
    });

    expect(await screen.findByText('This account has no membership.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Become a member' })).toHaveAttribute(
      'href',
      '/account/create-membership',
    );
    expect(screen.queryByRole('link', { name: 'Membership' })).not.toBeInTheDocument();
  });

  it('the paid year runs from its first day to the day it is paid until', () => {
    expect(passed('2026-01-01', '2026-12-31', new Date('2026-07-02T12:00:00'))).toBeCloseTo(0.5, 1);
    // Joined in the middle of the year: its first day is the day joined.
    expect(passed('2026-07-01', '2026-12-31', new Date('2026-07-01T00:00:00'))).toBe(0);
    // Years before count as nothing: the bar is about the year paid for.
    expect(passed('2020-01-01', '2026-12-31', new Date('2026-01-01T00:00:00'))).toBe(0);
    expect(passed(null, '2026-12-31', new Date('2027-03-01T00:00:00'))).toBe(1);
  });
});

describe('my account: membership and billing', () => {
  it('rejoining by invoice goes where the server says', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { origin: window.location.origin, href: window.location.href, assign });
    const { calls } = show(
      { 'POST /api/v1/account/rejoin': { body: { url: '/thank-you?method=invoice', message: null } } },
      '/account/membership',
    );

    const membership = await screen.findByRole('region', { name: 'Membership' });
    expect(within(membership).getByText(/You can rejoin at any time/)).toBeInTheDocument();
    await userEvent.click(within(membership).getByRole('radio', { name: 'Invoice' }));
    await userEvent.click(screen.getByRole('button', { name: 'Rejoin' }));

    await waitFor(() => {
      expect(assign).toHaveBeenCalledWith('/thank-you?method=invoice');
    });
    expect(await sentBody(calls, 'POST', '/api/v1/account/rejoin')).toEqual({ payment_method: 'invoice' });
  });
});

describe('my account: profile', () => {
  it('reads first: both addresses, each with whether it is confirmed', async () => {
    show({}, '/account/profile');

    const contact = await screen.findByRole('region', { name: 'Contact details' });
    expect(within(contact).getByText('anna@edu.fh-joanneum.at')).toBeInTheDocument();
    expect(within(contact).getByText('Confirmed')).toBeInTheDocument();
    expect(within(contact).getByText('Not confirmed')).toBeInTheDocument();
    expect(within(contact).getAllByRole('button', { name: 'Send the link again' })).toHaveLength(1);
    expect(within(contact).getByText('Main 1, 8010 Graz, Austria')).toBeInTheDocument();
    expect(within(contact).queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('an address being changed is not said to be confirmed', async () => {
    const { contact } = await editContact();

    const field = within(contact).getByRole('textbox', { name: /University or company email/ });
    fireEvent.change(field, { target: { value: 'anna@elsewhere.example' } });

    expect(within(contact).queryByText('Not confirmed')).not.toBeInTheDocument();
    expect(within(contact).getByText('Confirmed')).toBeInTheDocument();
  });

  it('a refused contact detail shows at its field', async () => {
    const { contact } = await editContact({
      'PUT /api/v1/account/contact': {
        status: 400,
        body: {
          error: {
            code: 'validation_error',
            message: 'Please check the form.',
            fields: { phone_private: 'Invalid phone number format' },
          },
        },
      },
    });

    const phone = within(contact).getByRole('textbox', { name: 'Private phone' });
    await userEvent.clear(phone);
    await userEvent.type(phone, 'call me');
    await userEvent.click(within(contact).getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Invalid phone number format')).toBeInTheDocument();
  });

  it('saving goes back to reading, as saved, and says what the server says', async () => {
    const saved = { ...account, member: { ...member, contact: { ...member.contact, city: 'Vienna' } } };
    const { calls, contact } = await editContact({
      'PUT /api/v1/account/contact': {
        body: { messages: [{ tone: 'success', text: 'Saved.' }], account: saved },
      },
    });

    const city = within(contact).getByRole('textbox', { name: 'City' });
    await userEvent.clear(city);
    await userEvent.type(city, 'Vienna');
    await userEvent.click(within(contact).getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Saved.')).toBeInTheDocument();
    expect(await screen.findByText('Main 1, 8010 Vienna, Austria')).toBeInTheDocument();
    expect(await sentBody(calls, 'PUT', '/api/v1/account/contact')).toEqual({
      ...member.contact,
      city: 'Vienna',
    });
  });

  it('cancelling leaves the details as they were', async () => {
    const { contact } = await editContact();

    await userEvent.type(within(contact).getByRole('textbox', { name: 'City' }), 'xyz');
    await userEvent.click(within(contact).getByRole('button', { name: 'Cancel' }));

    expect(await screen.findByText('Main 1, 8010 Graz, Austria')).toBeInTheDocument();
  });

  it('the year group is asked only of those who have one', async () => {
    show({}, '/account/profile');

    const region = await screen.findByRole('region', { name: 'Name and membership type' });
    expect(within(region).getByText('LAV25')).toBeInTheDocument();
    await userEvent.click(within(region).getByRole('button', { name: 'Request a change' }));
    expect(within(region).getByRole('textbox', { name: /Year group/ })).toBeInTheDocument();
    await userEvent.click(within(region).getByRole('combobox', { name: 'Membership type' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Company or partner' }));

    expect(within(region).queryByRole('textbox', { name: /Year group/ })).not.toBeInTheDocument();
  });

  it('a waiting change request is shown, not the form', async () => {
    show(
      {
        '/api/v1/account': {
          body: {
            ...account,
            member: {
              ...member,
              change_request: {
                id: 4,
                salutation: 'Ms',
                title: null,
                first_name: 'Anna',
                last_name: 'Huber',
                member_category: 'student',
                member_category_label: 'Student',
                year_group: 'LAV25',
                note: 'Married.',
                username_could_become: 'HuberA_L25',
              },
            },
          },
        },
      },
      '/account/profile',
    );

    const region = await screen.findByRole('region', { name: 'Name and membership type' });
    expect(within(region).getByText('Anna Huber')).toBeInTheDocument();
    expect(within(region).getByText('Could become HuberA_L25')).toBeInTheDocument();
    expect(within(region).queryByRole('button', { name: 'Request a change' })).not.toBeInTheDocument();
    expect(within(region).queryByRole('button', { name: 'Send for review' })).not.toBeInTheDocument();
  });
});
