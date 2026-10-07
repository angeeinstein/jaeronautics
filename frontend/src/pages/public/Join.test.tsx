import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { CreateMembership } from '../account/CreateMembership';
import { Join } from './Join';
import { Cancel, ThankYou } from './Payment';

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

function link(slug: string, name: string, atSignup: boolean): Schemas['LegalTextLinkOut'] {
  return {
    slug,
    title: name,
    name,
    in_force_since: '2026-01-01',
    accepted_at_signup: atSignup,
    url: `/legal/${slug}`,
    pdf_url: `/legal/${slug}/pdf`,
  };
}

const statutes: Schemas['LegalTextOut'] = {
  title: 'Statuten',
  language: 'de',
  is_translation: false,
  version: '2026-01-01',
  revision: null,
  effective_from: '2026-01-01',
  in_force: '2026-01-01',
  html: '<p>Der Verein führt den Namen.</p>',
  contents: [],
  german_url: '/legal/statutes/de',
  english_url: null,
  english_elsewhere: false,
  others: [],
  pdf_url: '/legal/statutes/pdf',
  pdf_has_english: false,
};

const base: Answers = {
  '/api/v1/session': { body: { signed_in: false, csrf_token: 'token' } },
  '/api/v1/forms/options': { body: options },
  '/api/v1/legal': {
    body: {
      texts: [
        link('statutes', 'Statutes', true),
        link('privacy-policy', 'Privacy Policy', true),
        link('legal-notice', 'Legal Notice', false),
      ],
    },
  },
  '/api/v1/legal/statutes': { body: statutes },
};

async function sentBody(calls: Request[], method: string, path: string) {
  const sent = calls.find((call) => call.method === method && new URL(call.url).pathname === path);
  return sent ? ((await sent.clone().json()) as unknown) : undefined;
}

async function choose(label: string, option: string) {
  await userEvent.click(screen.getByRole('combobox', { name: new RegExp(`^${label}`) }));
  await userEvent.click(await screen.findByRole('option', { name: option }));
}

async function fillIn() {
  await choose('Salutation', 'Ms');
  const fields: [RegExp, string][] = [
    [/^First name/, 'Nora'],
    [/^Last name/, 'New'],
    [/^Year group/, 'LAV25'],
    [/^Street/, 'Main'],
    [/^House number/, '1'],
    [/^Postal code/, '8010'],
    [/^City/, 'Graz'],
    [/^Private email/, 'nora@example.com'],
    [/^Private phone/, '+43123'],
    [/^University or company email/, 'nora.new@edu.fh-joanneum.at'],
  ];
  // Typed whole: key by key, a long form is slow on a busy machine.
  for (const [name, value] of fields) {
    fireEvent.change(screen.getByRole('textbox', { name }), { target: { value } });
  }
  fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: 'a-good-password' } });
  await userEvent.click(screen.getByRole('checkbox'));
}

describe('joining', () => {
  it('sends the form and goes where the server says', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { origin: window.location.origin, href: window.location.href, assign });
    const { calls } = mockFetch({
      ...base,
      'POST /api/v1/signup': { body: { go_to: 'https://checkout.stripe.test/1' } },
    });
    renderPage(<Join />);

    await screen.findByRole('textbox', { name: /^First name/ });
    await fillIn();
    await userEvent.click(screen.getByRole('button', { name: 'Join and continue to payment' }));

    await vi.waitFor(() => {
      expect(assign).toHaveBeenCalledWith('https://checkout.stripe.test/1');
    });
    expect(await sentBody(calls, 'POST', '/api/v1/signup')).toEqual({
      salutation: 'Ms',
      title: null,
      first_name: 'Nora',
      last_name: 'New',
      street: 'Main',
      house_number: '1',
      postal_code: '8010',
      city: 'Graz',
      country: 'Austria',
      phone_private: '+43123',
      phone_work: null,
      email_private: 'nora@example.com',
      email_work: 'nora.new@edu.fh-joanneum.at',
      member_category: 'student',
      year_group: 'LAV25',
      password: 'a-good-password',
      payment_method: 'checkout',
      terms_accepted: true,
    });
  });

  it('the year group and the university address are asked only of those who need them', async () => {
    mockFetch(base);
    renderPage(<Join />);

    const universityEmail = await screen.findByRole('textbox', { name: /^University or company email/ });
    expect(universityEmail).toBeRequired();
    await choose('Membership type', 'Company or partner');

    expect(screen.queryByRole('textbox', { name: /^Year group/ })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /^University or company email/ })).not.toBeRequired();
  });

  it('the server’s word on each field is shown at the field', async () => {
    mockFetch({
      ...base,
      'POST /api/v1/signup': {
        status: 400,
        body: {
          error: {
            code: 'invalid',
            message: 'Please correct the marked fields.',
            fields: { phone_private: 'Invalid phone number format', terms_accepted: 'Please accept.' },
          },
        },
      },
    });
    renderPage(<Join />);

    await userEvent.click(await screen.findByRole('button', { name: 'Join and continue to payment' }));

    expect(await screen.findByText('Invalid phone number format')).toBeInTheDocument();
    expect(screen.getByText('Please accept.')).toBeInTheDocument();
    expect(screen.queryByText('Please correct the marked fields.')).not.toBeInTheDocument();
  });

  it('an address with an account already is pointed to signing in', async () => {
    mockFetch({
      ...base,
      'POST /api/v1/signup': {
        status: 409,
        body: {
          error: { code: 'account_exists', message: 'An account with this email address already exists.' },
        },
      },
    });
    renderPage(<Join />);

    await userEvent.click(await screen.findByRole('button', { name: 'Join and continue to payment' }));

    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText(/already exists/)).toBeInTheDocument();
    expect(within(alert).getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login');
  });

  it('each text to accept opens over the form, the others are not asked', async () => {
    mockFetch(base);
    renderPage(<Join />);

    const statutesLink = await screen.findByRole('link', { name: 'Statutes' });
    expect(screen.getByRole('link', { name: 'Privacy Policy' })).toHaveAttribute(
      'href',
      '/legal/privacy-policy',
    );
    expect(screen.queryByRole('link', { name: 'Legal Notice' })).not.toBeInTheDocument();
    await userEvent.click(statutesLink);

    expect(await screen.findByText('Der Verein führt den Namen.')).toBeInTheDocument();
    expect(screen.getByRole('checkbox')).not.toBeChecked();
  });

  it('paying by invoice, when it is offered, says so', async () => {
    mockFetch({ ...base, '/api/v1/forms/options': { body: { ...options, invoice_payments: true } } });
    renderPage(<Join />);

    await userEvent.click(await screen.findByRole('radio', { name: 'Invoice' }));

    expect(screen.getByRole('button', { name: 'Join' })).toBeInTheDocument();
    expect(screen.getByText('We email you the invoice.')).toBeInTheDocument();
  });
});

describe('a membership for a login without one', () => {
  const account = {
    email: { address: 'staff@example.org', confirmed: true },
    to_confirm: null,
    member: null,
    export_url: '/account/data-export',
  };

  it('uses the login’s address and asks for no password', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { origin: window.location.origin, href: window.location.href, assign });
    const { calls } = mockFetch({
      ...base,
      '/api/v1/account': { body: account },
      'POST /api/v1/account/membership': { body: { go_to: '/thank-you?method=invoice&phase=prorated' } },
    });
    renderPage(<CreateMembership />);

    const address = await screen.findByRole('textbox', { name: /^Private email/ });
    expect(address).toHaveValue('staff@example.org');
    expect(address).toHaveAttribute('readonly');
    expect(screen.queryByLabelText(/^Password/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Start membership and continue to payment' }));

    await vi.waitFor(() => {
      expect(assign).toHaveBeenCalledWith('/thank-you?method=invoice&phase=prorated');
    });
    const sent = (await sentBody(calls, 'POST', '/api/v1/account/membership')) as Record<string, unknown>;
    expect(sent).not.toHaveProperty('email_private');
    expect(sent).not.toHaveProperty('password');
  });
});

describe('back from paying', () => {
  it('an invoice in the free months', async () => {
    mockFetch({ '/api/v1/site': { body: { footer: [], copyright: '', teams_label: null } } });
    renderPage(<ThankYou />, { route: '/thank-you?method=invoice&phase=free_period' });

    expect(await screen.findByText(/free until 31 December/)).toBeInTheDocument();
    expect(screen.getByText(/first annual invoice/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /See the/ })).not.toBeInTheDocument();
  });

  it('a card or SEPA payment, with the teams pointed to', async () => {
    mockFetch({ '/api/v1/site': { body: { footer: [], copyright: '', teams_label: 'Projects' } } });
    renderPage(<ThankYou />, { route: '/thank-you?phase=prorated' });

    expect(await screen.findByText(/once the debit clears/)).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: 'See the projects' })).toHaveAttribute('href', '/teams');
  });

  it('cancelled: nothing charged, and the way back', () => {
    mockFetch({});
    renderPage(<Cancel />);

    expect(screen.getByText(/You have not been charged/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go to My Account' })).toHaveAttribute('href', '/account');
  });
});
