import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { Devices } from './Devices';

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/teams/rocket/manage/devices';

function fridge(overrides: Partial<Schemas['OwnedDeviceOut']> = {}): Schemas['OwnedDeviceOut'] {
  return {
    id: 7,
    code: 'K7MQ-2X9A',
    name: null,
    label: 'K7MQ-2X9A',
    type_label: 'Sales point',
    place: null,
    description: null,
    enabled: true,
    online: true,
    last_seen_at: '2026-10-13T11:00:00Z',
    settings: { item: null },
    fields: [
      {
        name: 'item',
        label: 'Item',
        kind: 'choice',
        default: null,
        help: "What a tap sells, from the owner's price list.",
        max_length: 200,
        minimum: null,
        maximum: null,
        owner_may_change: true,
        choices: [{ value: 9, label: 'Mate, €2.50', group: 'Rocket', owner: 'rocket' }],
      },
    ],
    recent: [{ at: '2026-10-13T11:00:00Z', detail: '…E5F6 bought Mate, €2.50; €2.50 left' }],
    ...overrides,
  };
}

function show(answers: Answers = {}) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    [API]: { body: { items: [fridge()] } },
    ...answers,
  });
  renderPage(<Devices />, { route: '/teams/rocket/manage/devices', path: '/teams/:slug/manage/devices' });
  return fetched;
}

describe("a team's devices", () => {
  it('each device, how it is doing, and what it reported lately', async () => {
    show();

    const card = await screen.findByRole('region', { name: 'K7MQ-2X9A' });
    expect(card).toHaveTextContent('Online');
    expect(card).toHaveTextContent('Sales point');
    expect(within(card).getByRole('table', { name: 'What K7MQ-2X9A reported lately' })).toHaveTextContent(
      '…E5F6 bought Mate, €2.50; €2.50 left',
    );
  });

  it("is named and given an item from the team's own price list", async () => {
    const { calls } = show({
      [`PATCH ${API}/7`]: {
        body: { items: [fridge({ name: 'Rocket fridge', label: 'Rocket fridge', settings: { item: 9 } })] },
      },
    });

    const card = await screen.findByRole('region', { name: 'K7MQ-2X9A' });
    await userEvent.type(within(card).getByRole('textbox', { name: /^Name/ }), 'Rocket fridge');
    await userEvent.click(within(card).getByRole('combobox', { name: 'Item' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Mate, €2.50' }));
    await userEvent.click(within(card).getByRole('button', { name: 'Save' }));

    expect(await screen.findByRole('region', { name: 'Rocket fridge' })).toBeInTheDocument();
    const sent = calls.filter((call) => call.method === 'PATCH').at(-1);
    expect(await sent?.clone().json()).toEqual({
      name: 'Rocket fridge',
      place: '',
      description: '',
      settings: { item: 9 },
    });
  });

  it('a setting only an admin changes is not sent with its name', async () => {
    const test = fridge({
      type_label: 'Test',
      settings: { greeting: 'Hello' },
      fields: [],
      recent: [],
    });
    const { calls } = show({
      [API]: { body: { items: [test] } },
      [`PATCH ${API}/7`]: { body: { items: [{ ...test, name: 'Bench', label: 'Bench' }] } },
    });

    const card = await screen.findByRole('region', { name: 'K7MQ-2X9A' });
    await userEvent.type(within(card).getByRole('textbox', { name: /^Name/ }), 'Bench');
    await userEvent.click(within(card).getByRole('button', { name: 'Save' }));

    expect(await screen.findByRole('region', { name: 'Bench' })).toBeInTheDocument();
    const sent = calls.filter((call) => call.method === 'PATCH').at(-1);
    expect(await sent?.clone().json()).toEqual({ name: 'Bench', place: '', description: '' });
  });

  it('is switched off', async () => {
    const { calls } = show({ [`PATCH ${API}/7`]: { body: { items: [fridge({ enabled: false })] } } });

    const card = await screen.findByRole('region', { name: 'K7MQ-2X9A' });
    await userEvent.click(within(card).getByRole('switch', { name: 'On' }));

    await waitFor(() => {
      expect(screen.getByRole('region', { name: 'K7MQ-2X9A' })).toHaveTextContent('Off');
    });
    const sent = calls.filter((call) => call.method === 'PATCH').at(-1);
    expect(await sent?.clone().json()).toEqual({ enabled: false });
  });

  it('none yet: an admin adds them', async () => {
    show({ [API]: { body: { items: [] } } });
    expect(await screen.findByText('No devices yet. An admin adds them.')).toBeInTheDocument();
  });
});
