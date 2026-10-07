import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { makeMe } from '../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { Account } from './Account';

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
    },
    {
      value: 'partner',
      label: 'Company or partner',
      description: 'Joined on behalf of a company.',
      year_group: 'hidden',
      university_email_required: false,
    },
  ],
  invoice_payments: false,
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

function show(answers: Answers = {}) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    '/api/v1/account': { body: account },
    '/api/v1/forms/options': { body: options },
    ...answers,
  });
  renderPage(<Account />, { route: '/account', path: '/account' });
  return fetched;
}

async function sentBody(calls: Request[], method: string, path: string) {
  const sent = calls.find((call) => call.method === method && new URL(call.url).pathname === path);
  return sent ? ((await sent.clone().json()) as unknown) : undefined;
}

describe('my account', () => {
  it('first what is to be done, then the membership and what to do about it', async () => {
    show();

    expect(await screen.findByText(/Please confirm your university email address/)).toBeInTheDocument();
    const membership = screen.getByRole('region', { name: 'Membership' });
    expect(within(membership).getByText('Ended')).toBeInTheDocument();
    expect(within(membership).getByText(/You can rejoin at any time/)).toBeInTheDocument();
    expect(within(membership).getByRole('radio', { name: 'Invoice' })).toBeInTheDocument();
    // The addresses once, in the contact details, each with whether it is confirmed.
    expect(screen.queryByRole('region', { name: 'Email addresses' })).not.toBeInTheDocument();
    const contact = await screen.findByRole('region', { name: 'Contact details' });
    expect(within(contact).getByText('Confirmed')).toBeInTheDocument();
    expect(within(contact).getByText('Not confirmed')).toBeInTheDocument();
    expect(within(contact).getAllByRole('button', { name: 'Send the link again' })).toHaveLength(1);
    // The password is changed from the menu at the top right.
    expect(screen.queryByRole('link', { name: 'Change password' })).not.toBeInTheDocument();
  });

  it('an address being changed is not said to be confirmed', async () => {
    show({});

    const contact = await screen.findByRole('region', { name: 'Contact details' });
    const field = within(contact).getByRole('textbox', { name: /University or company email/ });
    fireEvent.change(field, { target: { value: 'anna@elsewhere.example' } });

    expect(within(contact).queryByText('Not confirmed')).not.toBeInTheDocument();
    expect(within(contact).getByText('Confirmed')).toBeInTheDocument();
  });

  it('rejoining by invoice goes where the server says', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { origin: window.location.origin, href: window.location.href, assign });
    const { calls } = show({
      'POST /api/v1/account/rejoin': { body: { url: '/thank-you?method=invoice', message: null } },
    });

    await userEvent.click(await screen.findByRole('radio', { name: 'Invoice' }));
    await userEvent.click(screen.getByRole('button', { name: 'Rejoin' }));

    await waitFor(() => {
      expect(assign).toHaveBeenCalledWith('/thank-you?method=invoice');
    });
    expect(await sentBody(calls, 'POST', '/api/v1/account/rejoin')).toEqual({ payment_method: 'invoice' });
  });

  it('a refused contact detail shows at its field', async () => {
    show({
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

    const phone = await screen.findByRole('textbox', { name: 'Private phone' });
    await userEvent.clear(phone);
    await userEvent.type(phone, 'call me');
    await userEvent.click(
      within(screen.getByRole('region', { name: 'Contact details' })).getByRole('button', { name: 'Save' }),
    );

    expect(await screen.findByText('Invalid phone number format')).toBeInTheDocument();
  });

  it('saving takes the page back and says what the server says', async () => {
    const saved = { ...account, member: { ...member, contact: { ...member.contact, city: 'Vienna' } } };
    const { calls } = show({
      'PUT /api/v1/account/contact': {
        body: { messages: [{ tone: 'success', text: 'Saved.' }], account: saved },
      },
    });

    const city = await screen.findByRole('textbox', { name: 'City' });
    await userEvent.clear(city);
    await userEvent.type(city, 'Vienna');
    await userEvent.click(
      within(screen.getByRole('region', { name: 'Contact details' })).getByRole('button', { name: 'Save' }),
    );

    expect(await screen.findByText('Saved.')).toBeInTheDocument();
    expect(await sentBody(calls, 'PUT', '/api/v1/account/contact')).toEqual({
      ...member.contact,
      city: 'Vienna',
    });
  });

  it('the year group is asked only of those who have one', async () => {
    show();

    const region = await screen.findByRole('region', { name: 'Name and membership type' });
    expect(within(region).getByRole('textbox', { name: /Year group/ })).toBeInTheDocument();
    await userEvent.click(within(region).getByRole('combobox', { name: 'Membership type' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Company or partner' }));

    expect(within(region).queryByRole('textbox', { name: /Year group/ })).not.toBeInTheDocument();
  });

  it('a waiting change request is shown, not the form', async () => {
    show({
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
    });

    const region = await screen.findByRole('region', { name: 'Name and membership type' });
    expect(within(region).getByText('Anna Huber')).toBeInTheDocument();
    expect(within(region).getByText('Could become HuberA_L25')).toBeInTheDocument();
    expect(within(region).queryByRole('button', { name: 'Send for review' })).not.toBeInTheDocument();
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
    expect(screen.queryByRole('region', { name: 'Contact details' })).not.toBeInTheDocument();
  });
});
