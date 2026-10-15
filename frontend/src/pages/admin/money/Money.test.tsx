import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { Money } from './Money';
import { centsOf } from './shared';
import { TeamMoney } from './TeamMoney';

type MoneyOut = Schemas['MoneyOut'];
type OverviewOut = Schemas['OverviewOut'];
type TeamMoneyOut = Schemas['TeamMoneyOut'];

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/admin/money';
const rocketRef = { slug: 'rocket', name: 'Rocket Team', status: 'active' as const, logo_url: null };

const list: MoneyOut = {
  teams: [
    {
      team: rocketRef,
      earned: 123450,
      paid_out: 100000,
      open: 23450,
      last_transfer_on: '2026-09-01',
      account: 'AT61 … 3201',
    },
    {
      team: { slug: 'glider', name: 'Glider Team', status: 'active', logo_url: null },
      earned: 1000,
      paid_out: 0,
      open: 1000,
      last_transfer_on: null,
      account: null,
    },
  ],
  total_open: 24450,
  today: '2026-10-06',
};

const zero = { received: 0, returned: 0, refunded: 0, taken_back: 0, fees: 0, net: 0 };

const overview: OverviewOut = {
  since: '2026-01-01',
  until: '2026-10-06',
  books: {
    membership: { ...zero, received: 500000, fees: 7000, net: 493000 },
    team: { ...zero, received: 30000, refunded: 1000, fees: 500, net: 28500 },
    credit: { ...zero, received: 4000, fees: 110, net: 3890 },
    other: zero,
    total: { ...zero, received: 534000, refunded: 1000, fees: 7610, net: 525390 },
    stripe_fees: 0,
    other_bookings: 0,
    paid_out: 400000,
    balance_at_end: 125390,
  },
  owed_to_teams: 24450,
  credit_held: { top_ups: 2500, cash: 700 },
  association_own: 98440,
  fetched_at: '2026-10-06T09:58:00Z',
  fetching: false,
  stripe_balance: 125390,
  books_balance: 125390,
  last_error: null,
  checked_at: '2026-10-06T09:58:00Z',
  differences: [
    {
      id: 1,
      product: 'team',
      what: 'Paid in Stripe, missing in the portal',
      reference: 'in_123',
      amount: null,
      stripe_amount: 1000,
      who: 'payer@example.org',
      first_seen_at: '2026-10-06T08:00:00Z',
      emailed_at: null,
      resolved_at: null,
    },
  ],
  resolved: [],
  emails: true,
};

const statement: TeamMoneyOut['statement'] = {
  teams_carry_fees: true,
  rows: [
    { kind: 'fee_paid', label: 'Fees paid', amount: 1000 },
    { kind: 'refunded', label: 'Refunded', amount: -400 },
    { kind: 'stripe_fee', label: "Stripe's fees on fees", amount: -35 },
  ],
  earned: 565,
  waiting: 0,
  transferred: 0,
  open: 565,
  lines: [
    {
      day: '2026-10-01',
      kind: 'fee_paid',
      text: 'Fee paid by Lena Lead, paid until 31.03.2027',
      amount: 1000,
      group: 'payment:1',
      detail: '',
      waiting: false,
    },
    {
      day: '2026-10-01',
      kind: 'refunded',
      text: 'Refunded',
      amount: -400,
      group: 'payment:1',
      detail: '',
      waiting: false,
    },
    {
      day: '2026-10-01',
      kind: 'stripe_fee',
      text: "Stripe's fee",
      amount: -35,
      group: 'payment:1',
      detail: 'kept by Stripe also when the payment is refunded',
      waiting: false,
    },
  ],
};

function team(overrides: Partial<TeamMoneyOut> = {}): TeamMoneyOut {
  return {
    team: rocketRef,
    earned: 3100,
    fees: 3100,
    sales: { earned: 0, count: 0, by_item: [] },
    waiting_for_fee: 0,
    paid_out: 0,
    open: 3100,
    statement,
    bank: { account_holder: 'Rocket Team', iban: 'AT611904300234573201', bic: null },
    reference: 'Teambeiträge Rocket Team bis 06.10.2026',
    today: '2026-10-06',
    by_period: [{ paid_until: '2027-03-31', earned: 3100 }],
    transfers: [],
    payments: [
      {
        id: 1,
        paid_at: '2026-10-01T08:00:00Z',
        name: 'Lena Lead',
        paid_until: '2027-03-31',
        amount: 1000,
        refunded: 400,
        disputed: false,
        fee: 35,
        fee_carried_by: 'association',
        counts: 600,
      },
    ],
    export_url: '/teams/rocket/money.csv',
    ...overrides,
  };
}

function show(element: React.ReactNode, route: string, path: string, answers: Answers = {}) {
  const fetched = mockFetch({
    [API]: { body: list },
    [`${API}/overview`]: { body: overview },
    [`${API}/rocket`]: { body: team() },
    '/api/v1/me': { body: makeMe() },
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    ...answers,
  });
  renderPage(element, { route, path });
  return fetched;
}

function asked(calls: Request[], path: string): Request[] {
  return calls.filter((call) => new URL(call.url).pathname === path);
}

describe('the list', () => {
  it('what each team is owed, each opening its money', async () => {
    show(<Money />, '/admin/money', '/admin/money');

    const table = await screen.findByRole('table', { name: 'What the teams are owed' });
    const [, rocket, glider] = within(table).getAllByRole('row');
    expect(rocket).toHaveTextContent('Rocket Team€1,234.50€1,000.00€234.5001.09.2026AT61 … 3201');
    expect(glider).toHaveTextContent('Missing');
    expect(within(table).getByRole('link', { name: 'Rocket Team' })).toHaveAttribute(
      'href',
      '/admin/money/rocket',
    );
    expect(screen.getByText('€244.50')).toBeInTheDocument();
  });

  it('the money on Stripe is shown at once, by period', async () => {
    const { calls } = show(<Money />, '/admin/money', '/admin/money');

    expect(await screen.findByText(/^Fetched from Stripe/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '2026' }));

    await waitFor(() => {
      const searches = asked(calls, `${API}/overview`).map((request) => new URL(request.url).search);
      expect(searches).toContain('?since=2026-01-01&until=2026-10-06');
    });
  });

  it('the totals, whose the money is, and what differs', async () => {
    show(<Money />, '/admin/money?since=2026-01-01', '/admin/money');

    const books = await screen.findByRole('table', { name: 'By what it was for' });
    expect(within(books).getByRole('row', { name: /^Net/ })).toHaveTextContent(
      '€4,930.00€285.00€38.90€0.00€5,253.90',
    );
    expect(within(books).getByRole('row', { name: /^Refunded/ })).toHaveTextContent('-€10.00');
    expect(screen.getByText('Its bookings add up to what Stripe holds.')).toBeInTheDocument();
    const held = screen.getByRole('table', { name: 'On 06.10.2026' });
    expect(within(held).getByRole('row', { name: /association's own/ })).toHaveTextContent('€984.40');
    expect(screen.getByText(/€7.00 of credit handed over in cash/)).toBeInTheDocument();
    expect(screen.getByText('1 difference:')).toBeInTheDocument();
    expect(
      screen.getByText(/Paid in Stripe, missing in the portal · in_123 · Stripe €10.00 · payer@example.org/),
    ).toBeInTheDocument();
  });

  it('while the first fetch runs, no figures but what is happening', async () => {
    show(<Money />, '/admin/money', '/admin/money', {
      [`${API}/overview`]: { body: { ...overview, fetched_at: null, fetching: true } },
    });

    expect(await screen.findByText(/Fetching Stripe’s book for the first time/)).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'By what it was for' })).toBeNull();
  });

  it('fetching now answers with the overview', async () => {
    const { calls } = show(<Money />, '/admin/money', '/admin/money', {
      [`POST ${API}/fetch`]: { body: { ...overview, differences: [] } },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Fetch from Stripe now' }));

    await waitFor(() => {
      expect(asked(calls, `${API}/fetch`)).toHaveLength(1);
    });
    expect(
      await screen.findByText(
        "Every payment in Stripe matches the portal's records, and the other way round.",
      ),
    ).toBeInTheDocument();
  });

  it('Stripe out of reach is said so', async () => {
    show(<Money />, '/admin/money', '/admin/money', {
      [`POST ${API}/fetch`]: {
        status: 502,
        body: {
          error: {
            code: 'stripe_unavailable',
            message: 'Stripe could not be asked. Please try again in a few minutes.',
            fields: null,
            details: null,
          },
        },
      },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Fetch from Stripe now' }));

    expect(
      await screen.findByText('Stripe could not be asked. Please try again in a few minutes.'),
    ).toBeInTheDocument();
  });
});

describe('one team', () => {
  const route = ['/admin/money/rocket', '/admin/money/:slug'] as const;

  it('the balance, the payments and the export', async () => {
    show(<TeamMoney />, ...route);

    expect(await screen.findByRole('heading', { name: 'Rocket Team', level: 1 })).toBeInTheDocument();
    // The payments are under History; the balance first.
    await userEvent.click(screen.getByRole('tab', { name: 'History' }));
    expect(screen.getByRole('table', { name: 'Payments' })).toHaveTextContent(
      '01.10.2026Lena Lead31.03.2027€10.00€0.35carried by the association€6.00€4.00 refunded',
    );
    expect(screen.getByRole('link', { name: 'Export' })).toHaveAttribute('href', '/teams/rocket/money.csv');
  });

  it('how the figure adds up, every line on request', async () => {
    show(<TeamMoney />, ...route);

    const sums = await screen.findByRole('table', { name: 'How it adds up' });
    expect(within(sums).getByRole('row', { name: /^Stripe's fees on fees/ })).toHaveTextContent('-€0.35');
    expect(within(sums).getByRole('row', { name: /^Open/ })).toHaveTextContent('€5.65');
    await userEvent.click(screen.getByRole('button', { name: 'Every line (3)' }));
    expect(await screen.findByText('kept by Stripe also when the payment is refunded')).toBeInTheDocument();
  });

  it('a transfer is recorded after a second click, with the amount as typed', async () => {
    const { calls } = show(<TeamMoney />, ...route, {
      [`POST ${API}/rocket/transfers`]: { status: 201, body: team({ open: 1100, paid_out: 2000 }) },
    });

    const amount = await screen.findByRole('textbox', { name: 'Amount (€)' });
    expect(amount).toHaveValue('31.00');
    await userEvent.clear(amount);
    await userEvent.type(amount, '20,00');
    await userEvent.click(screen.getByRole('button', { name: 'Mark as transferred' }));
    expect(asked(calls, `${API}/rocket/transfers`)).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Yes, record €20.00' }));

    await waitFor(() => {
      expect(asked(calls, `${API}/rocket/transfers`)).toHaveLength(1);
    });
    const [request] = asked(calls, `${API}/rocket/transfers`);
    expect(await request?.clone().json()).toEqual({
      amount: '20,00',
      paid_on: '2026-10-06',
      reference: 'Teambeiträge Rocket Team bis 06.10.2026',
    });
    expect(await screen.findByText('Transfer recorded.')).toBeInTheDocument();
  });

  it('the code to scan follows the amount', async () => {
    show(<TeamMoney />, ...route);

    const code = await screen.findByRole('img', { name: 'Transfer code for €31.00' });
    expect(code.getAttribute('src')).toBe(
      '/admin/money/rocket/transfer-code.svg?amount=3100&reference=Teambeitr%C3%A4ge+Rocket+Team+bis+06.10.2026',
    );
  });

  it('no code without bank details', async () => {
    show(<TeamMoney />, ...route, {
      [`${API}/rocket`]: { body: team({ bank: { account_holder: null, iban: null, bic: null } }) },
    });

    expect(
      await screen.findByText('The team has no bank details yet, so there is no code to scan.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('img', { name: /Transfer code/ })).toBeNull();
  });

  it('nothing open, nothing to transfer', async () => {
    show(<TeamMoney />, ...route, { [`${API}/rocket`]: { body: team({ open: 0, paid_out: 3100 }) } });

    expect(await screen.findByText('Nothing open.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Mark as transferred' })).toBeNull();
  });

  it('a mistyped IBAN is shown at the IBAN', async () => {
    show(<TeamMoney />, ...route, {
      [`PUT ${API}/rocket/bank`]: {
        status: 400,
        body: {
          error: {
            code: 'team_iban_invalid',
            message: 'That IBAN is mistyped: its check digits do not match.',
            fields: null,
            details: null,
          },
        },
      },
    });

    const iban = await screen.findByRole('textbox', { name: 'IBAN' });
    await userEvent.type(iban, '9');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(
      await screen.findByText('That IBAN is mistyped: its check digits do not match.'),
    ).toBeInTheDocument();
    expect(iban).toHaveAttribute('aria-invalid', 'true');
  });
});

describe('reading an amount', () => {
  it.each([
    ['240', 24000],
    ['240.5', 24050],
    ['240,00', 24000],
    ['1.240,00', 124000],
    ['€ 12', 1200],
    ['abc', null],
    ['1.234', null],
    ['', null],
  ])('%s is %s cents', (text, cents) => {
    expect(centsOf(text)).toBe(cents);
  });
});
