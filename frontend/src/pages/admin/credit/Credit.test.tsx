import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { CreditSettings, parseAmounts } from '../settings/CreditSettings';
import { Credit } from './Credit';
import { Holder } from './Holder';

afterEach(() => {
  vi.unstubAllGlobals();
});

const base: Answers = {
  '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
  '/api/v1/me': { body: makeMe({ credit_admin: true }) },
};

const entry: Schemas['AdminCreditEntryOut'] = {
  id: 7,
  kind: 'cash_in',
  kind_label: 'Cash',
  description: 'At the office',
  amount_cents: 500,
  balance_after_cents: 1500,
  at: '2026-10-08T10:00:00Z',
  receipt_url: null,
  team_name: null,
  user_id: 3,
  name: 'Anna Berger',
  booked_by: 'Tom Treasurer',
};

const overview: Schemas['CreditOverviewOut'] = {
  enabled: true,
  totals: { held_cents: 1500, came_in_cents: 1500, spent_cents: 0, people: 1 },
  people: [{ user_id: 3, name: 'Anna Berger', picture_url: null, balance_cents: 1500, erased: false }],
  recent: [entry],
  mismatched: [],
  sellers: [{ name: 'Joanneum Aeronautics', team_slug: null, earned_30_days: 120, earned: 120, items: 1 }],
};

const holder: Schemas['CreditHolderOut'] = {
  user_id: 3,
  name: 'Anna Berger',
  email: 'anna@example.com',
  picture_url: null,
  erased: false,
  member: true,
  balance_cents: 1500,
  refundable_cents: 1000,
  max_balance_cents: 2000,
  entries: [entry],
  account_url: '/admin/accounts/3',
  items: [{ id: 9, name: 'Coffee', price_cents: 120, seller: 'Joanneum Aeronautics' }],
};

describe('admin: credit', () => {
  it('what is held, by whom, and the latest', async () => {
    mockFetch({ ...base, '/api/v1/admin/credit': { body: overview } });
    renderPage(<Credit />);

    expect(await screen.findByText('Held for members')).toBeInTheDocument();
    const balances = screen.getByRole('table', { name: 'Balances' });
    expect(within(balances).getByRole('link', { name: 'Anna Berger' })).toHaveAttribute(
      'href',
      '/admin/credit/3',
    );
    expect(
      within(screen.getByRole('table', { name: 'Latest entries' })).getByText('+€5.00'),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download all' })).toHaveAttribute(
      'href',
      '/admin/credit/export.csv',
    );
  });

  it('says when credit is off, and when balances do not add up', async () => {
    mockFetch({
      ...base,
      '/api/v1/admin/credit': { body: { ...overview, enabled: false, mismatched: [3] } },
    });
    renderPage(<Credit />);

    expect(await screen.findByText(/Credit is switched off/)).toBeInTheDocument();
    expect(screen.getByText('Balances that do not add up')).toBeInTheDocument();
  });

  it('one person: booking cash, and refunding on the second click', async () => {
    const { calls } = mockFetch({
      ...base,
      '/api/v1/admin/credit/3': { body: holder },
      'POST /api/v1/admin/credit/3/cash': { body: { ...holder, balance_cents: 2000 } },
      'POST /api/v1/admin/credit/3/refund': {
        body: { ...holder, balance_cents: 500, refundable_cents: 0, refunded_cents: 1000, left_cents: 500 },
      },
    });
    renderPage(<Holder />, { route: '/admin/credit/3', path: '/admin/credit/:userId' });

    expect(await screen.findByRole('heading', { level: 1, name: 'Anna Berger' })).toBeInTheDocument();
    expect(screen.getByText('Booked by Tom Treasurer')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Amount'), '5');
    await userEvent.click(screen.getByRole('button', { name: 'Book' }));
    await waitFor(() => {
      expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/cash'))).toBe(true);
    });
    const sent = calls.find((call) => call.url.endsWith('/cash'));
    expect(await sent?.clone().json()).toEqual({ amount_cents: 500, paid_out: false, note: null });

    const refund = screen.getByRole('button', { name: 'Refund €10.00' });
    await userEvent.click(refund);
    expect(calls.some((call) => call.url.endsWith('/refund'))).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Yes, refund €10.00' }));
    await waitFor(() => {
      expect(calls.some((call) => call.url.endsWith('/refund'))).toBe(true);
    });
  });

  it('a correction needs its reason', async () => {
    mockFetch({ ...base, '/api/v1/admin/credit/3': { body: holder } });
    renderPage(<Holder />, { route: '/admin/credit/3', path: '/admin/credit/:userId' });

    await userEvent.click(await screen.findByRole('radio', { name: 'Correction' }));
    await userEvent.type(screen.getByLabelText('Amount'), '-2');

    expect(screen.getByRole('button', { name: 'Book' })).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Why'), 'Counted twice');
    expect(screen.getByRole('button', { name: 'Book' })).toBeEnabled();
  });
});

describe('admin: selling by hand', () => {
  it('a sale from a price list, then taken back on the second click', async () => {
    const sold: typeof holder = {
      ...holder,
      balance_cents: 1380,
      entries: [
        {
          ...entry,
          id: 8,
          kind: 'purchase',
          kind_label: 'Purchase',
          description: 'Coffee',
          amount_cents: -120,
          balance_after_cents: 1380,
          seller: 'Joanneum Aeronautics',
          may_take_back: true,
        },
        entry,
      ],
    };
    const { calls } = mockFetch({
      ...base,
      '/api/v1/admin/credit/3': { body: holder },
      'POST /api/v1/admin/credit/3/sale': { body: sold },
      'POST /api/v1/admin/credit/3/entries/8/take-back': { body: holder },
    });
    renderPage(<Holder />, { route: '/admin/credit/3', path: '/admin/credit/:userId' });

    await userEvent.click(await screen.findByRole('radio', { name: 'Sale' }));
    await userEvent.click(screen.getByRole('combobox', { name: 'Item' }));
    await userEvent.click(await screen.findByRole('option', { name: /Coffee/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Book' }));
    await waitFor(() => {
      expect(calls.some((call) => call.url.endsWith('/sale'))).toBe(true);
    });
    expect(
      await calls
        .find((call) => call.url.endsWith('/sale'))
        ?.clone()
        .json(),
    ).toEqual({ item_id: 9 });

    await userEvent.click(await screen.findByRole('button', { name: 'Take back' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, take it back' }));
    await waitFor(() => {
      expect(calls.some((call) => call.url.endsWith('/take-back'))).toBe(true);
    });
  });
});

describe('settings: credit', () => {
  const settings: Schemas['CreditSettingsOut'] = {
    enabled: false,
    product_id: null,
    min_top_up_cents: 1000,
    max_balance_cents: 2000,
    suggested_cents: [1000, 1500, 2000],
    eps: false,
    stripe_ready: true,
  };

  it('sends euros as cents', async () => {
    const { calls } = mockFetch({
      ...base,
      '/api/v1/admin/settings/credit': { body: settings },
      'PUT /api/v1/admin/settings/credit': { body: { changed: ['credit_enabled'] } },
    });
    renderPage(<CreditSettings />);

    await userEvent.click(await screen.findByRole('checkbox', { name: /Credit is on/ }));
    await userEvent.type(screen.getByLabelText('Stripe product'), 'prod_123');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PUT')).toBe(true);
    });
    const sent = calls.find((call) => call.method === 'PUT');
    expect(await sent?.clone().json()).toEqual({
      enabled: true,
      product_id: 'prod_123',
      min_top_up_cents: 1000,
      max_balance_cents: 2000,
      suggested_cents: [1000, 1500, 2000],
      eps: false,
    });
  });

  it('suggested amounts read as typed', () => {
    expect(parseAmounts('10, 15, 20')).toEqual([1000, 1500, 2000]);
    expect(parseAmounts('€2.50;5')).toEqual([250, 500]);
    expect(parseAmounts('ten')).toBeNull();
  });
});
