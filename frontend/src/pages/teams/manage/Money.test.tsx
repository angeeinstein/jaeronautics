import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { Money } from './Money';

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/teams/rocket/money';

const funds: Schemas['TeamFundsOut'] = {
  slug: 'rocket',
  name: 'Rocket',
  earned: 12000,
  fees: 12000,
  sales: { earned: 0, count: 0, by_item: [] },
  waiting_for_fee: 0,
  paid_out: 8000,
  open: 4000,
  last_transfer_on: '2026-09-01',
  bank: { account_holder: 'Rocket Team', iban: 'AT611904300234573201', bic: null },
  may_edit_bank: true,
  records_transfers: false,
  by_period: [{ paid_until: '2027-03-31', earned: 12000 }],
  transfers: [{ id: 1, paid_on: '2026-09-01', amount: 8000, reference: 'Rocket 2026', to: 'AT61 … 3201' }],
  payments: [
    {
      id: 3,
      paid_at: '2026-08-10T10:00:00Z',
      name: 'Anna Berger',
      paid_until: '2027-03-31',
      amount: 6000,
      refunded: 0,
      disputed: false,
      counts: 6000,
    },
  ],
  export_url: '/teams/rocket/money.csv',
};

function show(answers: Answers = {}) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    [API]: { body: funds },
    ...answers,
  });
  renderPage(<Money />, { route: '/teams/rocket/money', path: '/teams/:slug/money' });
  return fetched;
}

describe("a team's money", () => {
  it('the balance, the transfers and the payments', async () => {
    show();

    expect(await screen.findByText('€40.00')).toBeInTheDocument();
    const transfers = screen.getByRole('table', { name: 'Transfers' });
    expect(within(transfers).getByText('Rocket 2026')).toBeInTheDocument();
    const payments = screen.getByRole('table', { name: 'Payments' });
    expect(within(payments).getByText('Anna Berger')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Export payments' })).toHaveAttribute(
      'href',
      '/teams/rocket/money.csv',
    );
    expect(screen.queryByRole('link', { name: 'Transfers (Admin)' })).not.toBeInTheDocument();
  });

  it('who may not change the bank details sees them shortened, and no form', async () => {
    show({
      [API]: {
        body: {
          ...funds,
          may_edit_bank: false,
          records_transfers: true,
          bank: { ...funds.bank, iban: 'AT61 … 3201' },
        },
      },
    });

    expect(await screen.findByText('Rocket Team, AT61 … 3201')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'IBAN' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Transfers (Admin)' })).toHaveAttribute(
      'href',
      '/admin/money/rocket',
    );
  });

  it('saves the bank details', async () => {
    const saved = { ...funds, bank: { ...funds.bank, bic: 'BKAUATWW' } };
    const { calls } = show({ [`PUT ${API}/bank`]: { body: { changed: true, funds: saved } } });

    await userEvent.type(await screen.findByRole('textbox', { name: 'BIC (optional)' }), 'BKAUATWW');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Bank details saved.')).toBeInTheDocument();
    await waitFor(async () => {
      const sent = calls.find((call) => call.method === 'PUT');
      expect(await sent?.clone().json()).toEqual({
        account_holder: 'Rocket Team',
        iban: 'AT611904300234573201',
        bic: 'BKAUATWW',
      });
    });
  });

  it('a wrong IBAN shows at the field', async () => {
    show({
      [`PUT ${API}/bank`]: {
        status: 400,
        body: {
          error: {
            code: 'validation_error',
            message: 'Please check the form.',
            fields: { iban: 'This IBAN is not valid.' },
          },
        },
      },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Save' }));

    expect(await screen.findByText('This IBAN is not valid.')).toBeInTheDocument();
  });
});
