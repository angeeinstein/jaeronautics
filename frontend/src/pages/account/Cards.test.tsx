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

/** A phone that reads ``serial`` from the next card held to it (Web NFC). */
function phoneReading(serial: string) {
  class Reader {
    onreading: ((event: { serialNumber: string }) => void) | null = null;
    onreadingerror: (() => void) | null = null;
    scan() {
      setTimeout(() => this.onreading?.({ serialNumber: serial }), 0);
      return Promise.resolve();
    }
  }
  vi.stubGlobal('NDEFReader', Reader);
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

  it('a card scanned with the phone is linked', async () => {
    phoneReading('04:a1:b2:c3:d4:e5:f6');
    const { calls } = mockFetch({
      ...base,
      '/api/v1/account/cards': { body: { items: [], most: 10 } },
      'POST /api/v1/account/cards': { status: 201, body: { items: [card({ name: 'Fob' })], most: 10 } },
    });
    renderPage(<Cards />);

    await userEvent.type(await screen.findByRole('textbox', { name: 'Name (optional)' }), 'Fob');
    await userEvent.click(screen.getByRole('button', { name: 'Scan card' }));

    expect(await screen.findByRole('listitem', { name: 'Fob' })).toBeInTheDocument();
    const sent = calls.filter((call) => call.method === 'POST').at(-1);
    expect(await sent?.clone().json()).toEqual({ card_id: '04:a1:b2:c3:d4:e5:f6', name: 'Fob' });
  });

  it('a browser that cannot read cards says where it can be done', async () => {
    mockFetch({ ...base, '/api/v1/account/cards': { body: { items: [], most: 10 } } });
    renderPage(<Cards />);

    expect(await screen.findByText(/This browser cannot read cards/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Scan card' })).not.toBeInTheDocument();
  });
});

describe("an account's cards, for admins", () => {
  const person = (overrides: Partial<Schemas['PersonCardsOut']> = {}): Schemas['PersonCardsOut'] => ({
    user_id: 7,
    items: [card()],
    readers: [{ id: 3, name: 'Fridge reader', place: 'Workshop' }],
    waiting: null,
    most: 10,
    ...overrides,
  });

  it('waits at a reader for the card, and stops', async () => {
    const { calls } = mockFetch({
      ...base,
      '/api/v1/admin/accounts/7/cards': { body: person() },
      'POST /api/v1/admin/accounts/7/cards/wait': {
        body: person({
          waiting: {
            reader: { id: 3, name: 'Fridge reader', place: 'Workshop' },
            expires_at: new Date(Date.now() + 60_000).toISOString(),
          },
        }),
      },
      'DELETE /api/v1/admin/accounts/7/cards/wait': { body: person() },
    });
    renderPage(<CardsTab userId={7} />);

    await userEvent.click(await screen.findByRole('button', { name: 'Wait for a tap' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Tap the card on Fridge reader');
    const sent = calls.filter((call) => call.method === 'POST').at(-1);
    expect(await sent?.clone().json()).toEqual({ device_id: 3 });

    await userEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(await screen.findByRole('button', { name: 'Wait for a tap' })).toBeInTheDocument();
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

  it('types an ID in', async () => {
    const { calls } = mockFetch({
      ...base,
      '/api/v1/admin/accounts/7/cards': { body: person({ items: [] }) },
      'POST /api/v1/admin/accounts/7/cards': { status: 201, body: person() },
    });
    renderPage(<CardsTab userId={7} />);

    await userEvent.type(await screen.findByRole('textbox', { name: 'Card ID' }), '04A1B2C3D4E5F6');
    await userEvent.click(screen.getByRole('button', { name: 'Link' }));

    expect(await screen.findByRole('listitem', { name: 'Key fob' })).toBeInTheDocument();
    const sent = calls.filter((call) => call.method === 'POST').at(-1);
    expect(await sent?.clone().json()).toEqual({ card_id: '04A1B2C3D4E5F6', name: null });
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
