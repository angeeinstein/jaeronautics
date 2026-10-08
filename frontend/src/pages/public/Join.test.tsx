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

function kind(
  value: string,
  label: string,
  more: Partial<Schemas['MemberCategoryOut']> = {},
): Schemas['MemberCategoryOut'] {
  return {
    value,
    label,
    description: `${label}, as the server says it.`,
    year_group: 'hidden',
    university_email_required: false,
    work_email_at_joining: false,
    work_email_whose: null,
    account_email: 'private',
    company_name: false,
    joinable: true,
    ...more,
  };
}

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
    kind('student', 'Student', {
      year_group: 'required',
      university_email_required: true,
      work_email_at_joining: true,
      work_email_whose: 'student',
    }),
    kind('alumni', 'Alumni', { year_group: 'optional' }),
    kind('staff', 'Staff or lecturer', {
      work_email_at_joining: true,
      work_email_whose: 'staff',
      account_email: 'private_or_institute',
    }),
    kind('partner', 'Company or partner', { company_name: true, account_email: 'any' }),
    kind('honorary', 'Honorary member', { joinable: false }),
  ],
  invoice_payments: false,
  student_domains: ['edu.fh-joanneum.at'],
  staff_domains: ['fh-joanneum.at'],
  programmes: [
    { code: 'LAV', name: 'Aviation', degree: 'Bachelor' },
    { code: 'MAV', name: 'Aviation', degree: 'Master' },
  ],
};

const price: Schemas['SignupPriceOut'] = {
  price: { annual_fee: '€40.00', due_today: '€20.05', paid_until: '2026-12-31', renews_on: '2027-01-01' },
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
  '/api/v1/signup/price': { body: price },
};

async function sentBody(calls: Request[], method: string, path: string) {
  const sent = calls.find((call) => call.method === method && new URL(call.url).pathname === path);
  return sent ? ((await sent.clone().json()) as unknown) : undefined;
}

function type(name: RegExp, value: string) {
  // Typed whole: key by key, a long form is slow on a busy machine.
  fireEvent.change(screen.getByRole('textbox', { name }), { target: { value } });
}

async function next(heading: string) {
  await userEvent.click(screen.getByRole('button', { name: 'Continue' }));
  return screen.findByRole('heading', { name: heading });
}

/** Which kind of member, then on by itself to the next step. */
async function pick(name: RegExp) {
  await userEvent.click(await screen.findByRole('radio', { name }));
  return screen.findByRole('heading', { name: 'About you' });
}

async function aboutAStudent() {
  await pick(/^Student/);
  await userEvent.click(screen.getByRole('radio', { name: 'Ms' }));
  type(/^First name/, 'Nora');
  type(/^Last name/, 'New');
  await userEvent.click(screen.getByRole('radio', { name: /^Aviation · Bachelor/ }));
  await userEvent.click(screen.getByRole('radio', { name: '2025' }));
  type(/^University email/, 'nora.new@edu.fh-joanneum.at');
}

async function contact({ signup = true } = {}) {
  await next('How we reach you');
  if (signup) type(/^Private email/, 'nora@example.com');
  type(/^Phone/, '+43123');
  type(/^Street/, 'Main');
  type(/^House number/, '1');
  type(/^Postal code/, '8010');
  type(/^City/, 'Graz');
}

async function toTheLastStep() {
  await aboutAStudent();
  await contact();
  await next('Your password');
  fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: 'a-good-password' } });
  return next('Check and join');
}

describe('joining, step by step', () => {
  it('first which kind of member: the appointed kinds are not offered', async () => {
    mockFetch(base);
    renderPage(<Join />);

    // On arriving the page is read from the top; the focus moves with the steps only.
    expect(await screen.findByRole('heading', { name: 'Which describes you?' })).not.toHaveFocus();
    expect(screen.getAllByRole('radio').map((radio) => radio.textContent)).toEqual([
      expect.stringMatching(/^Student/),
      expect.stringMatching(/^Alumni/),
      expect.stringMatching(/^Staff or lecturer/),
      expect.stringMatching(/^Company or partner/),
    ]);
    expect(screen.getByRole('navigation', { name: 'Steps' })).toHaveTextContent(/Password/);
  });

  it('goes through the steps, then where the server says', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { origin: window.location.origin, href: window.location.href, assign });
    const { calls } = mockFetch({
      ...base,
      'POST /api/v1/signup': { body: { go_to: 'https://checkout.stripe.test/1' } },
    });
    renderPage(<Join />);

    await toTheLastStep();
    expect(screen.getByRole('heading', { name: 'Check and join' })).toHaveFocus();
    const summary = screen.getByText('You are').closest('dl');
    expect(summary).toHaveTextContent('Student · LAV25');
    expect(summary).toHaveTextContent('Ms Nora New');
    expect(summary).toHaveTextContent('NewN_L25');
    const cost = screen.getByRole('region', { name: 'What it costs' });
    expect(cost).toHaveTextContent('€20.05');
    expect(cost).toHaveTextContent('€40.00');
    expect(cost).toHaveTextContent('31.12.2026');
    await userEvent.click(screen.getByRole('checkbox'));
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
      company_name: null,
      member_category: 'student',
      year_group: 'LAV25',
      password: 'a-good-password',
      terms_accepted: true,
    });
  });

  it('the forum name appears as it is typed', async () => {
    mockFetch(base);
    renderPage(<Join />);

    await aboutAStudent();

    expect(screen.getByText('NewN_L25')).toBeInTheDocument();
  });

  it('a step goes on only with what it needs, said at each field', async () => {
    mockFetch(base);
    renderPage(<Join />);
    await pick(/^Student/);

    await userEvent.click(screen.getByRole('button', { name: 'Continue' }));

    expect(screen.getByRole('heading', { name: 'About you' })).toBeInTheDocument();
    expect(screen.getByText('Please enter your first name.')).toBeInTheDocument();
    expect(screen.getByText('Please choose your programme and the year you started.')).toBeInTheDocument();
    expect(screen.getByText('Please enter your university address.')).toBeInTheDocument();
    // A staff address is not a student's, though it is the university's.
    type(/^University email/, 'nora@fh-joanneum.at');
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(screen.getByText('Please use your student address: @edu.fh-joanneum.at.')).toBeInTheDocument();
  });

  it('the year group: a programme by name or by its code, and the year started', async () => {
    mockFetch(base);
    renderPage(<Join />);
    await pick(/^Student/);

    await userEvent.click(screen.getByRole('radio', { name: /^Aviation · Master/ }));
    await userEvent.click(screen.getByRole('radio', { name: '2024' }));
    expect(screen.getByText('MAV24')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: /^Another programme/ }));
    type(/^Programme code/, 'bm1i');
    expect(screen.getByRole('textbox', { name: /^Programme code/ })).toHaveValue('BMI');
    expect(screen.getByText('BMI24')).toBeInTheDocument();
  });

  it('a company member names the company and signs in with the company address', async () => {
    const { calls } = mockFetch({
      ...base,
      'POST /api/v1/signup': { body: { go_to: 'https://checkout.stripe.test/3' } },
    });
    vi.stubGlobal('location', {
      origin: window.location.origin,
      href: window.location.href,
      assign: vi.fn(),
    });
    renderPage(<Join />);

    await pick(/^Company or partner/);
    expect(screen.queryByRole('radio', { name: /^Aviation/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: /email/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: 'Mr' }));
    type(/^First name/, 'Rudi');
    type(/^Last name/, 'Rep');
    type(/^Company$/, 'Some Airline');
    await next('How we reach you');
    // Any address: a company's is what they join with.
    type(/^Email/, 'rudi.rep@fh-joanneum.at');
    type(/^Phone/, '+43123');
    type(/^Street/, 'Main');
    type(/^House number/, '1');
    type(/^Postal code/, '8010');
    type(/^City/, 'Graz');
    await next('Your password');
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: 'a-good-password' } });
    await next('Check and join');
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: 'Join and continue to payment' }));

    await vi.waitFor(async () => {
      expect(await sentBody(calls, 'POST', '/api/v1/signup')).toMatchObject({
        member_category: 'partner',
        company_name: 'Some Airline',
        email_private: 'rudi.rep@fh-joanneum.at',
        email_work: null,
        year_group: null,
      });
    });
  });

  it('staff give their institute address, and sign in with it or a private one', async () => {
    const { calls } = mockFetch({
      ...base,
      'POST /api/v1/signup': { body: { go_to: 'https://checkout.stripe.test/4' } },
    });
    vi.stubGlobal('location', {
      origin: window.location.origin,
      href: window.location.href,
      assign: vi.fn(),
    });
    renderPage(<Join />);

    await pick(/^Staff or lecturer/);
    await userEvent.click(screen.getByRole('radio', { name: 'Ms' }));
    type(/^First name/, 'Lena');
    type(/^Last name/, 'Lecturer');
    type(/^Institute email/, 'lena.lecturer@edu.fh-joanneum.at');
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(screen.getByText('Please use your institute address: @fh-joanneum.at.')).toBeInTheDocument();
    type(/^Institute email/, 'lena.lecturer@fh-joanneum.at');
    await next('How we reach you');

    expect(screen.getByRole('radio', { name: /My institute address/ })).toBeChecked();
    expect(screen.queryByRole('textbox', { name: /^Private email/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: 'A private address' }));
    type(/^Private email/, 'lena@fh-joanneum.at');
    type(/^Phone/, '+43123');
    type(/^Street/, 'Main');
    type(/^House number/, '1');
    type(/^Postal code/, '8010');
    type(/^City/, 'Graz');
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(screen.getByText(/To sign in with your institute address, choose it above/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: /My institute address/ }));
    await next('Your password');
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: 'a-good-password' } });
    await next('Check and join');
    expect(screen.getByText('Sign-in email').closest('div')).toHaveTextContent(
      'lena.lecturer@fh-joanneum.at',
    );
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: 'Join and continue to payment' }));

    await vi.waitFor(async () => {
      expect(await sentBody(calls, 'POST', '/api/v1/signup')).toMatchObject({
        member_category: 'staff',
        email_private: 'lena.lecturer@fh-joanneum.at',
        email_work: 'lena.lecturer@fh-joanneum.at',
      });
    });
  });

  it('an alumnus: the year group if remembered, the university address only if it still works', async () => {
    mockFetch(base);
    renderPage(<Join />);

    await pick(/^Alumni/);

    expect(screen.getByRole('radio', { name: 'I don’t remember' })).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: /^University email/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'My university address still works' }));
    expect(screen.getByRole('textbox', { name: /^University email/ })).not.toBeRequired();
  });

  it('back keeps what was typed, and a line of the last step leads to its step', async () => {
    mockFetch(base);
    renderPage(<Join />);

    await toTheLastStep();
    await userEvent.click(screen.getByRole('button', { name: 'Change phone' }));

    expect(await screen.findByRole('heading', { name: 'How we reach you' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /^Phone/ })).toHaveValue('+43123');
    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByRole('heading', { name: 'About you' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /^First name/ })).toHaveValue('Nora');
  });

  it('the server’s word on a field takes the form back to its step', async () => {
    mockFetch({
      ...base,
      'POST /api/v1/signup': {
        status: 400,
        body: {
          error: {
            code: 'invalid',
            message: 'Please correct the marked fields.',
            fields: { phone_private: 'Invalid phone number format' },
          },
        },
      },
    });
    renderPage(<Join />);

    await toTheLastStep();
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: 'Join and continue to payment' }));

    expect(await screen.findByRole('heading', { name: 'How we reach you' })).toBeInTheDocument();
    expect(screen.getByText('Invalid phone number format')).toBeInTheDocument();
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

    await toTheLastStep();
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: 'Join and continue to payment' }));

    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText(/already exists/)).toBeInTheDocument();
    expect(within(alert).getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login');
  });

  it('each text to accept opens over the form, the others are not asked', async () => {
    mockFetch(base);
    renderPage(<Join />);

    await toTheLastStep();
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

  it('in the free months nothing is due today; without Stripe the payment page says it', async () => {
    mockFetch({
      ...base,
      '/api/v1/signup/price': { body: { price: { ...price.price, due_today: null } } },
    });
    const { unmount } = renderPage(<Join />);

    await toTheLastStep();
    expect(screen.getByRole('region', { name: 'What it costs' })).toHaveTextContent(
      /NothingFree until 31\.12\.2026/,
    );
    unmount();

    mockFetch({ ...base, '/api/v1/signup/price': { body: { price: null } } });
    renderPage(<Join />);
    await toTheLastStep();
    expect(await screen.findByText('The payment page shows what it costs.')).toBeInTheDocument();
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
      'POST /api/v1/account/membership': { body: { go_to: 'https://checkout.stripe.test/2' } },
    });
    renderPage(<CreateMembership />);

    expect(await screen.findByRole('navigation', { name: 'Steps' })).not.toHaveTextContent(/Password/);
    await aboutAStudent();
    await contact({ signup: false });
    const address = screen.getByRole('textbox', { name: /^Sign-in email/ });
    expect(address).toHaveValue('staff@example.org');
    expect(address).toHaveAttribute('readonly');
    await next('Check and join');
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: 'Start membership and continue to payment' }));

    await vi.waitFor(() => {
      expect(assign).toHaveBeenCalledWith('https://checkout.stripe.test/2');
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

    expect(screen.getByText(/Nothing was charged/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go to My Account' })).toHaveAttribute('href', '/account');
  });
});
