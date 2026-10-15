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
    items: [
      {
        item_id: 1,
        listed: true,
        name: 'Beer',
        icon: 'beer',
        sold_30_days: 3,
        earned_30_days: 600,
        sold: 5,
        earned: 1000,
      },
    ],
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

  it('each item with what it sold and made, 30 days or ever; what was sold of a deleted item', async () => {
    showTeam(['team.edit_prices'], {
      '/api/v1/teams/rocket/manage/sales': {
        body: seller({
          items: [
            ...seller().items,
            {
              item_id: 9,
              listed: false,
              name: 'Old cola',
              icon: 'soft_drink',
              sold_30_days: 0,
              earned_30_days: 0,
              sold: 2,
              earned: 300,
            },
          ],
        }),
      },
    });

    const table = await screen.findByRole('table', { name: 'Price list' });
    await waitFor(() => {
      expect(within(table).getByRole('row', { name: /^Beer/ })).toHaveTextContent('€2.003€6.00');
    });
    await userEvent.click(screen.getByText('Ever'));
    expect(within(table).getByRole('row', { name: /^Beer/ })).toHaveTextContent('€2.005€10.00');
    expect(within(table).getByRole('row', { name: /Old cola/ })).toHaveTextContent(
      'No longer on the list2€3.00',
    );
    expect(within(table).getByRole('row', { name: /Together/ })).toHaveTextContent('5€10.00');
  });

  it('an item deleted after asking once more', async () => {
    const { calls } = showTeam(['team.edit_prices'], {
      'DELETE /api/v1/teams/rocket/manage/prices/1': { body: { ...list, items: [list.items[1]] } },
    });

    const table = await screen.findByRole('table', { name: 'Price list' });
    await userEvent.click(within(table).getByRole('button', { name: 'Delete Beer' }));
    await userEvent.click(within(table).getByRole('button', { name: 'Yes, delete' }));
    await waitFor(() => {
      expect(calls.some((call) => call.method === 'DELETE')).toBe(true);
    });
    await waitFor(() => {
      expect(within(table).queryByRole('row', { name: /^Beer/ })).not.toBeInTheDocument();
    });
  });

  it('an item’s name: at most 20 characters, counted', async () => {
    showTeam(['team.edit_prices']);
    const name = await screen.findByLabelText('New item');
    expect(name).toHaveAttribute('maxlength', '20');
    await userEvent.type(name, 'Club-Mate');
    expect(screen.getByText('9/20')).toBeInTheDocument();
  });

  it('each sales point’s menu, on a tab of its own, sent on its own', async () => {
    const { calls } = showTeam(['team.edit_prices', 'team.devices'], {
      '/api/v1/teams/rocket/manage/devices': { body: { items: [salesPoint()] } },
      'PATCH /api/v1/teams/rocket/manage/devices/7': { body: { items: [salesPoint()] } },
    });

    await userEvent.click(await screen.findByRole('tab', { name: 'Sales points' }));
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
  it('by seller; the association’s price list with its figures; no tab without a sales point', async () => {
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
                  item_id: 1,
                  listed: true,
                  name: 'Beer',
                  icon: 'beer',
                  sold_30_days: 4,
                  earned_30_days: 800,
                  sold: 4,
                  earned: 800,
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
    const prices = await screen.findByRole('table', { name: "The association's price list" });
    expect(within(prices).getByRole('row', { name: /^Beer/ })).toHaveTextContent('4€8.00');
    expect(screen.queryByRole('tab', { name: 'Sales points' })).not.toBeInTheDocument();
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
