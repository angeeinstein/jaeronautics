import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { FeeSettings } from '../money/FeeSettings';
import { SalesPanel } from '../money/sales';
import { Prices } from '../../teams/manage/Prices';

afterEach(() => {
  vi.unstubAllGlobals();
});

const base: Answers = {
  '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
  '/api/v1/me': { body: makeMe() },
};

const list: Schemas['PriceListOut'] = {
  enabled: true,
  items: [
    { id: 1, name: 'Beer', price_cents: 200, active: true },
    { id: 2, name: 'Mate', price_cents: 250, active: false },
  ],
};

describe('a team’s price list', () => {
  it('lists, adds and switches off', async () => {
    const { calls } = mockFetch({
      ...base,
      '/api/v1/teams/rocket/manage': { body: { name: 'Rocket', labels: { plural: 'Teams' } } },
      '/api/v1/teams/rocket/manage/prices': { body: list },
      'POST /api/v1/teams/rocket/manage/prices': { status: 201, body: list },
      'PUT /api/v1/teams/rocket/manage/prices/1': { body: list },
    });
    renderPage(<Prices />, { route: '/teams/rocket/manage/prices', path: '/teams/:slug/manage/prices' });

    const table = await screen.findByRole('table', { name: 'Price list' });
    expect(within(table).getByText('€2.00')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('New item'), 'Red Bull');
    await userEvent.type(screen.getByLabelText('Price'), '2.5');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => {
      expect(calls.some((call) => call.method === 'POST')).toBe(true);
    });
    const sent = calls.find((call) => call.method === 'POST');
    expect(await sent?.clone().json()).toEqual({ name: 'Red Bull', price_cents: 250 });

    await userEvent.click(screen.getByRole('switch', { name: 'Sell Beer' }));
    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PUT')).toBe(true);
    });
    expect(
      await calls
        .find((call) => call.method === 'PUT')
        ?.clone()
        .json(),
    ).toEqual({ active: false });
  });
});

describe('what a team sold', () => {
  it('by item, with the total', () => {
    renderPage(
      <SalesPanel
        sales={{
          earned: 400,
          count: 2,
          by_item: [{ name: 'Beer', count: 2, earned: 400 }],
        }}
      />,
    );
    const table = screen.getByRole('table', { name: 'Sold for credit' });
    expect(within(table).getByRole('row', { name: /Beer/ })).toHaveTextContent('€4.00');
    expect(within(table).getByRole('row', { name: /Together/ })).toHaveTextContent('2');
  });
});

describe('who carries Stripe’s fees', () => {
  it('sends the share in hundredths of a percent', async () => {
    const { calls } = mockFetch({
      ...base,
      '/api/v1/admin/money/settings': { body: { fee_payer: 'association', credit_share_bps: 0 } },
      'PUT /api/v1/admin/money/settings': { body: { changed: ['team_fees_stripe_fee_payer'] } },
    });
    renderPage(<FeeSettings />);

    await userEvent.click(await screen.findByText('Taken from the team'));
    await userEvent.type(screen.getByLabelText('Kept of what teams sell for credit'), '3');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PUT')).toBe(true);
    });
    expect(
      await calls
        .find((call) => call.method === 'PUT')
        ?.clone()
        .json(),
    ).toEqual({
      fee_payer: 'team',
      credit_share_bps: 300,
    });
  });
});
