import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { FeeSettings } from '../money/FeeSettings';
import { SalesPanel } from '../money/sales';
import { Sales } from '../../teams/manage/Sales';
import { Sales as AdminSales } from './Sales';

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
    { id: 1, name: 'Beer', price_cents: 200, icon: 'beer', active: true },
    { id: 2, name: 'Mate', price_cents: 250, icon: 'mate', active: false },
  ],
  icons: [
    { value: 'beer', label: 'Beer' },
    { value: 'energy_drink', label: 'Energy drink' },
    { value: 'other', label: 'Other' },
  ],
};

function seller(overrides: Partial<Schemas['SellerSalesOut']> = {}): Schemas['SellerSalesOut'] {
  return {
    name: 'Rocket',
    team_slug: 'rocket',
    listed: 1,
    sold_30_days: 3,
    earned_30_days: 600,
    sold: 5,
    earned: 1000,
    items: [{ name: 'Beer', icon: 'beer', sold_30_days: 3, earned_30_days: 600, sold: 5, earned: 1000 }],
    ...overrides,
  };
}

function salesPoint(): Schemas['OwnedDeviceOut'] {
  return {
    id: 7,
    code: 'K7MQ-2X9A',
    name: 'Rocket fridge',
    label: 'Rocket fridge',
    type_label: 'Sales point',
    place: 'Workshop',
    description: null,
    enabled: true,
    online: true,
    last_seen_at: '2026-10-13T11:00:00Z',
    settings: { items: [] },
    fields: [
      {
        name: 'items',
        label: 'Items',
        kind: 'choices',
        most: 4,
        with_prices: true,
        default: [],
        help: '',
        max_length: 200,
        minimum: null,
        maximum: null,
        owner_may_change: true,
        choices: [{ value: 1, label: 'Beer, €2.00', group: 'Rocket', owner: 'rocket' }],
      },
    ],
    recent: [],
  };
}

function showTeam(permissions: string[], answers: Answers = {}) {
  const fetched = mockFetch({
    ...base,
    '/api/v1/teams/rocket/manage': { body: { name: 'Rocket', labels: { plural: 'Teams' }, permissions } },
    '/api/v1/teams/rocket/manage/prices': { body: list },
    '/api/v1/teams/rocket/manage/sales': { body: seller() },
    ...answers,
  });
  renderPage(<Sales />, { route: '/teams/rocket/manage/sales', path: '/teams/:slug/manage/sales' });
  return fetched;
}

describe('a team’s Sales page', () => {
  it('its price list: lists, adds and switches off', async () => {
    const { calls } = showTeam(['team.edit_prices'], {
      'POST /api/v1/teams/rocket/manage/prices': { status: 201, body: list },
      'PUT /api/v1/teams/rocket/manage/prices/1': { body: list },
    });

    const table = await screen.findByRole('table', { name: 'Price list' });
    expect(within(table).getByText('€2.00')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('New item'), 'Red Bull');
    await userEvent.type(screen.getByLabelText('Price'), '2.5');
    // Not without an icon: it is the picture on a sales point's screen.
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled();
    await userEvent.click(screen.getByRole('combobox', { name: 'Icon' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Energy drink' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => {
      expect(calls.some((call) => call.method === 'POST')).toBe(true);
    });
    const sent = calls.find((call) => call.method === 'POST');
    expect(await sent?.clone().json()).toEqual({ name: 'Red Bull', price_cents: 250, icon: 'energy_drink' });

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

  it('what each item sold and made, and each sales point’s menu, sent on its own', async () => {
    const { calls } = showTeam(['team.edit_prices', 'team.devices'], {
      '/api/v1/teams/rocket/manage/devices': { body: { items: [salesPoint()] } },
      'PATCH /api/v1/teams/rocket/manage/devices/7': { body: { items: [salesPoint()] } },
    });

    const sold = await screen.findByRole('table', { name: 'Sold by item' });
    expect(within(sold).getByRole('row', { name: /Beer/ })).toHaveTextContent('3€6.005€10.00');
    const menu = await screen.findByRole('region', { name: 'Rocket fridge' });
    await userEvent.click(within(menu).getByRole('combobox', { name: 'Items' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Beer, €2.00' }));
    await userEvent.click(within(menu).getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PATCH')).toBe(true);
    });
    const patch = calls.find((call) => call.method === 'PATCH');
    expect(await patch?.clone().json()).toEqual({ settings: { items: [1] } });
  });
});

describe('Admin › Sales', () => {
  it('by seller, then by item for the seller chosen; the association’s price list', async () => {
    mockFetch({
      ...base,
      '/api/v1/admin/sales': {
        body: {
          sellers: [
            seller({
              name: 'Joanneum Aeronautics',
              team_slug: null,
              items: [
                {
                  name: 'Coffee',
                  icon: 'coffee',
                  sold_30_days: 4,
                  earned_30_days: 320,
                  sold: 9,
                  earned: 720,
                },
              ],
            }),
            seller(),
          ],
        },
      },
      '/api/v1/admin/credit/items': { body: list },
      '/api/v1/admin/credit/devices': { body: { items: [] } },
    });
    renderPage(<AdminSales />);

    const sellers = await screen.findByRole('table', { name: 'Sold by seller' });
    expect(within(sellers).getByRole('link', { name: 'Rocket' })).toHaveAttribute(
      'href',
      '/admin/money/rocket',
    );
    expect(screen.getByRole('table', { name: 'Sold by item: Joanneum Aeronautics' })).toHaveTextContent(
      'Coffee',
    );
    await userEvent.click(screen.getByRole('combobox', { name: 'Seller' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Rocket' }));
    expect(screen.getByRole('table', { name: 'Sold by item: Rocket' })).toHaveTextContent('Beer');
    expect(await screen.findByRole('table', { name: "The association's price list" })).toBeInTheDocument();
    // No sales point of its own yet: no menus.
    expect(screen.queryByText('Sales points')).not.toBeInTheDocument();
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
  it('sends who carries them, leaving the email switch as it is', async () => {
    const { calls } = mockFetch({
      ...base,
      '/api/v1/admin/money/settings': {
        body: { fee_payer: 'association', difference_emails: false },
      },
      'PUT /api/v1/admin/money/settings': { body: { changed: ['team_fees_stripe_fee_payer'] } },
    });
    renderPage(<FeeSettings />);

    await userEvent.click(await screen.findByText('The teams'));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PUT')).toBe(true);
    });
    expect(
      await calls
        .find((call) => call.method === 'PUT')
        ?.clone()
        .json(),
    ).toEqual({ fee_payer: 'team', difference_emails: false });
  });
});
