import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { eased } from '../../lib/countUp';
import { makeMe } from '../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { Credit } from './Credit';
import { signedEuros } from './creditData';

afterEach(() => {
  vi.unstubAllGlobals();
});

const base: Answers = {
  '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
  '/api/v1/me': { body: makeMe({ credit_area: true }) },
};

function entry(overrides: Partial<Schemas['CreditEntryOut']> = {}): Schemas['CreditEntryOut'] {
  return {
    id: 1,
    kind: 'top_up',
    kind_label: 'Top-up',
    description: 'Top-up',
    amount_cents: 1500,
    balance_after_cents: 1500,
    at: '2026-10-08T10:00:00Z',
    receipt_url: '/account/credit/receipt/1',
    team_name: null,
    ...overrides,
  };
}

function credit(overrides: Partial<Schemas['MyCreditOut']> = {}): Schemas['MyCreditOut'] {
  return {
    balance_cents: 500,
    top_up: {
      refused: null,
      choices: [1000, 1500],
      least_cents: 1000,
      most_cents: 1500,
      max_balance_cents: 2000,
    },
    entries: [],
    export_url: '/account/credit/history.csv',
    ...overrides,
  };
}

describe('the credit page', () => {
  it('the balance, the amounts that fit, and topping up through Stripe', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { origin: window.location.origin, href: window.location.href, assign });
    const { calls } = mockFetch({
      ...base,
      '/api/v1/account/credit': { body: credit() },
      'POST /api/v1/account/credit/top-up': { body: { url: 'https://checkout.stripe.test/1' } },
    });
    renderPage(<Credit />);

    expect(await screen.findByText('€5.00')).toBeInTheDocument();
    const amounts = screen.getByRole('radiogroup', { name: 'How much' });
    expect(
      within(amounts)
        .getAllByRole('radio')
        .map((radio) => radio.textContent),
    ).toEqual(['€10.00', '€15.00', 'Other amount']);
    await userEvent.click(within(amounts).getByRole('radio', { name: '€10.00' }));
    await userEvent.click(screen.getByRole('button', { name: 'Top up €10.00' }));

    await waitFor(() => {
      expect(assign).toHaveBeenCalledWith('https://checkout.stripe.test/1');
    });
    const sent = calls.find((call) => call.method === 'POST');
    expect(await sent?.json()).toEqual({ amount_cents: 1000 });
  });

  it('another amount only within what may be topped up', async () => {
    mockFetch({ ...base, '/api/v1/account/credit': { body: credit() } });
    renderPage(<Credit />);

    await userEvent.click(await screen.findByRole('radio', { name: 'Other amount' }));
    await userEvent.type(screen.getByLabelText('Amount'), '25');

    expect(screen.getByText('Outside what you can top up.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Top up' })).toBeDisabled();
  });

  it('says why it cannot be topped up', async () => {
    mockFetch({
      ...base,
      '/api/v1/account/credit': {
        body: credit({
          top_up: {
            refused: 'Topping up is for members of the association.',
            choices: [],
            least_cents: 1000,
            most_cents: 0,
            max_balance_cents: 2000,
          },
        }),
      },
    });
    renderPage(<Credit />);

    expect(await screen.findByText('Topping up is for members of the association.')).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
  });

  it('the history, with receipts and the spreadsheet', async () => {
    mockFetch({
      ...base,
      '/api/v1/account/credit': {
        body: credit({
          entries: [
            entry({
              id: 2,
              kind: 'purchase',
              kind_label: 'Purchase',
              description: 'Coffee',
              amount_cents: -120,
              balance_after_cents: 1380,
              receipt_url: null,
            }),
            entry(),
          ],
        }),
      },
    });
    renderPage(<Credit />);

    const table = await screen.findByRole('table', { name: 'History' });
    expect(within(table).getByText('Coffee')).toBeInTheDocument();
    expect(within(table).getByText('−€1.20')).toBeInTheDocument();
    expect(within(table).getByText('+€15.00')).toBeInTheDocument();
    expect(within(table).getByRole('link', { name: 'Receipt' })).toHaveAttribute(
      'href',
      '/account/credit/receipt/1',
    );
    expect(screen.getByRole('link', { name: 'Download' })).toHaveAttribute(
      'href',
      '/account/credit/history.csv',
    );
  });

  it('back from Stripe: waits for the top-up, then says it is in', async () => {
    const answers: Answers = { ...base, '/api/v1/account/credit': { body: credit() } };
    mockFetch(answers);
    renderPage(<Credit />, { route: '/account/credit?topped_up=1' });

    expect(await screen.findByText('Payment received')).toBeInTheDocument();
    answers['/api/v1/account/credit'] = {
      body: credit({ balance_cents: 2000, entries: [entry({ at: new Date().toISOString() })] }),
    };
    await userEvent.click(screen.getByRole('button', { name: 'Check now' }));

    expect(await screen.findByText('€15.00 added')).toBeInTheDocument();
  });
});

describe('amounts', () => {
  it('signed, with a real minus', () => {
    expect(signedEuros(1500)).toBe('+€15.00');
    expect(signedEuros(-120)).toBe('−€1.20');
    expect(signedEuros(0)).toBe('€0.00');
  });

  it('counting up settles at the end', () => {
    expect(eased(0)).toBe(0);
    expect(eased(1)).toBe(1);
    expect(eased(0.5)).toBeGreaterThan(0.5);
  });
});
