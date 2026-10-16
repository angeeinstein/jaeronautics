import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { makeMe } from '../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { CardsTab } from '../admin/account/CardsTab';
import { Cards as AllCards } from '../admin/Cards';
import { Cards } from './Cards';

afterEach(() => {
  vi.unstubAllGlobals();
});

const base: Answers = {
  '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
  '/api/v1/me': { body: makeMe({ cards_area: true }) },
};

function card(overrides: Partial<Schemas['CardOut']> = {}): Schemas['CardOut'] {
  return {
    id: 1,
    name: 'Key fob',
    tail: '…E5F6',
    state: 'active',
    linked_at: '2026-10-13T08:00:00Z',
    linked_how: 'Scanned by its owner',
    last_used_at: '2026-10-13T09:00:00Z',
    last_used_on: 'Fridge reader',
    ...overrides,
  };
}

describe('my cards', () => {
  it('each card with the end of its ID, its state and where it was last used', async () => {
    mockFetch({ ...base, '/api/v1/account/cards': { body: { items: [card()], most: 10 } } });
    renderPage(<Cards />);

    const row = await screen.findByRole('listitem', { name: 'Key fob' });
    expect(row).toHaveTextContent('…E5F6');
    expect(row).toHaveTextContent('Active');
    expect(row).toHaveTextContent('Fridge reader');
  });

  it('switched off by its owner; a blocked one cannot be switched on', async () => {
    const { calls } = mockFetch({
      ...base,
      '/api/v1/account/cards': {
        body: { items: [card(), card({ id: 2, name: 'Student card', state: 'blocked' })], most: 10 },
      },
      'PATCH /api/v1/account/cards/1': { body: { items: [card({ state: 'disabled' })], most: 10 } },
    });
    renderPage(<Cards />);

    const blocked = await screen.findByRole('listitem', { name: 'Student card' });
    expect(within(blocked).getByRole('switch', { name: 'On' })).toBeDisabled();
    expect(blocked).toHaveTextContent('Blocked by an admin');

    await userEvent.click(within(screen.getByRole('listitem', { name: 'Key fob' })).getByRole('switch'));
    await waitFor(() => {
      expect(screen.getByRole('listitem', { name: 'Key fob' })).toHaveTextContent('Off');
    });
    const sent = calls.filter((call) => call.method === 'PATCH').at(-1);
    expect(await sent?.clone().json()).toEqual({ enabled: false });
  });

  it('a new card is linked at a sales point: the page says how', async () => {
    mockFetch({ ...base, '/api/v1/account/cards': { body: { items: [], most: 10 } } });
    renderPage(<Cards />);

    expect(await screen.findByText(/tap it on a sales point and scan the QR code/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Scan card' })).not.toBeInTheDocument();
  });
});

describe("an account's cards, for admins", () => {
  const person = (overrides: Partial<Schemas['PersonCardsOut']> = {}): Schemas['PersonCardsOut'] => ({
    user_id: 7,
    items: [card()],
    ...overrides,
  });

  it('blocks a card', async () => {
    const { calls } = mockFetch({
      ...base,
      '/api/v1/admin/accounts/7/cards': { body: person() },
      'PATCH /api/v1/admin/cards/1': { body: { ...card({ state: 'blocked' }), owner: owner() } },
    });
    renderPage(<CardsTab userId={7} />);

    const row = await screen.findByRole('listitem', { name: 'Key fob' });
    await userEvent.click(within(row).getByRole('button', { name: 'Block' }));
    await userEvent.click(within(row).getByRole('button', { name: 'Yes, block' }));
    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PATCH')).toBe(true);
    });
    const sent = calls.filter((call) => call.method === 'PATCH').at(-1);
    expect(await sent?.clone().json()).toEqual({ blocked: true });
  });

  it('nothing to link here: its owner does that at a reader', async () => {
    mockFetch({ ...base, '/api/v1/admin/accounts/7/cards': { body: person({ items: [] }) } });
    renderPage(<CardsTab userId={7} />);

    expect(await screen.findByText(/Its owner links one by tapping it on a sales point/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Wait for a tap' })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Card ID' })).not.toBeInTheDocument();
  });
});

function owner(): Schemas['CardOwnerOut'] {
  return { user_id: 7, name: 'Anna Berger', email: 'anna@example.com', picture_url: null };
}

describe('all cards', () => {
  it('lists whose each card is, and filters', async () => {
    mockFetch({
      ...base,
      '/api/v1/admin/cards': {
        body: {
          items: [
            { ...card(), owner: owner() },
            {
              ...card({ id: 2, name: 'Student card', tail: '…0001', last_used_at: null, last_used_on: null }),
              owner: { ...owner(), user_id: 8, name: 'Ben Huber', email: 'ben@example.com' },
            },
          ],
        },
      },
    });
    renderPage(<AllCards />);

    const table = await screen.findByRole('table', { name: 'Cards' });
    expect(within(table).getByRole('link', { name: 'Anna Berger' })).toHaveAttribute(
      'href',
      '/admin/accounts/7#cards',
    );
    expect(within(table).getAllByRole('row')).toHaveLength(3);

    await userEvent.type(screen.getByRole('textbox', { name: 'Search cards' }), 'ben');
    expect(within(table).getAllByRole('row')).toHaveLength(2);
    expect(table).toHaveTextContent('Not used yet');
  });
});
