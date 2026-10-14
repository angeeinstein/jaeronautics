import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { DeviceDetail } from './Device';
import { Devices } from './Devices';

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/admin/devices';
const OWNERS = [
  { value: 'association', label: 'Joanneum Aeronautics' },
  { value: 'rocket', label: 'Rocket' },
];
const SERVER = 'https://members.test/api/v1/device';

const testType: Schemas['DeviceTypeOut'] = {
  slug: 'test',
  label: 'Test',
  description: 'For trying a device out.',
  settings: [
    {
      name: 'greeting',
      label: 'Greeting',
      kind: 'text',
      default: 'Hello',
      help: '',
      max_length: 60,
      minimum: null,
      maximum: null,
    },
  ],
  resources: [{ name: 'hello', description: 'The greeting and the device’s name.' }],
  reports: [
    { name: 'ping', description: 'Answered with what it was sent.', tryable: true },
    { name: 'sale', description: 'A sale.', tryable: false },
  ],
};

const reader: Schemas['DeviceOut'] = {
  id: 3,
  code: 'K7MQ-2X9A',
  name: 'Fridge reader',
  label: 'Fridge reader',
  type: 'test',
  type_label: 'Test',
  owner: 'association',
  owner_label: 'Joanneum Aeronautics',
  place: 'Workshop',
  description: null,
  hardware_id: '24:6F:28:AA:BB:CC',
  settings: { greeting: 'Hello' },
  enabled: true,
  online: true,
  last_seen_at: '2026-10-12T07:00:00Z',
  last_address: '10.0.0.5',
  firmware: '0.1.0',
  key_hint: 'jadev_3f9a1c2b7d10_…AbCd',
  key_last_used_at: '2026-10-12T07:00:00Z',
  key_revoked_at: null,
  key_created_at: '2026-10-12T06:00:00Z',
  created_at: '2026-10-12T06:00:00Z',
};

function show(element: React.ReactNode, answers: Answers, route?: { route: string; path: string }) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    [API]: { body: { items: [reader], types: [testType], owners: OWNERS, server: SERVER } },
    ...answers,
  });
  renderPage(element, route);
  return fetched;
}

describe('devices', () => {
  it('each device with its state', async () => {
    show(<Devices />, {});

    const table = await screen.findByRole('table', { name: 'Devices' });
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent(
      'Fridge readerK7MQ-2X9A · WorkshopTestJoanneum AeronauticsOnline',
    );
    expect(within(table).getByRole('link', { name: 'Fridge reader' })).toHaveAttribute(
      'href',
      '/admin/settings/devices/3',
    );
  });

  it('adding one, for an owner: its key shown once, to copy, or download with a starter for its firmware', async () => {
    const { calls } = show(<Devices />, {
      [`POST ${API}`]: {
        status: 201,
        body: {
          device: { ...reader, id: 4, name: 'Door', online: false, last_seen_at: null },
          key: 'jadev_aaaaaaaaaaaa_secret',
          configuration: {
            server: SERVER,
            device_id: 4,
            name: 'Door',
            type: 'test',
            key: 'jadev_aaaaaaaaaaaa_secret',
          },
          starter: '# Firmware starter',
          starter_name: 'device-K7MQ-2X9A-starter.md',
        },
      },
    });

    await userEvent.type(await screen.findByRole('textbox', { name: /^Name/ }), 'Door');
    await userEvent.click(screen.getByRole('combobox', { name: 'Owner' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Rocket' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));

    expect(await screen.findByTestId('device-key')).toHaveTextContent('jadev_aaaaaaaaaaaa_secret');
    expect(screen.getByRole('button', { name: 'Copy key' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download configuration' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download starter' })).toBeInTheDocument();
    const sent = calls.filter((call) => call.method === 'POST' && new URL(call.url).pathname === API).at(-1);
    expect(await sent?.clone().json()).toEqual({
      name: 'Door',
      type: 'test',
      owner: 'rocket',
      place: '',
      settings: {},
    });

    await userEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(screen.queryByTestId('device-key')).not.toBeInTheDocument();
  });
});

describe('one device', () => {
  const detail = {
    device: reader,
    events: [],
    requests: [
      {
        id: 2,
        at: '2026-10-12T07:00:05Z',
        method: 'GET',
        path: '/api/v1/device/resources/items',
        status: 404,
        code: 'device_resource_unknown',
        address: '10.0.0.5',
      },
    ],
    server: SERVER,
  };
  const route = { route: '/admin/settings/devices/3', path: '/admin/settings/devices/:deviceId' };

  it('its key on one line: shortened, made, last used, with rotate and revoke beside it', async () => {
    show(<DeviceDetail />, { [`${API}/3`]: { body: detail } }, route);

    const card = await screen.findByRole('region', { name: 'Key' });
    expect(within(card).getByLabelText('Key')).toHaveTextContent('jadev_3f9a1c2b7d10_…AbCd');
    expect(card).toHaveTextContent('last used');
    expect(within(card).getByRole('button', { name: 'Rotate' })).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Revoke' })).toBeInTheDocument();
  });

  it('a rotated key is shown in the key card itself', async () => {
    show(
      <DeviceDetail />,
      {
        [`${API}/3`]: { body: detail },
        [`POST ${API}/3/key`]: {
          body: {
            device: reader,
            key: 'jadev_3f9a1c2b7d10_newsecret',
            configuration: { server: SERVER, device_id: 3, name: reader.name, type: 'test', key: 'x' },
          },
        },
      },
      route,
    );

    const card = await screen.findByRole('region', { name: 'Key' });
    await userEvent.click(within(card).getByRole('button', { name: 'Rotate' }));
    await userEvent.click(within(card).getByRole('button', { name: 'Yes, rotate' }));
    expect(await within(card).findByTestId('device-key')).toHaveTextContent('jadev_3f9a1c2b7d10_newsecret');
  });

  it('every request with its answer', async () => {
    show(<DeviceDetail />, { [`${API}/3`]: { body: detail } }, route);

    const table = await screen.findByRole('table', { name: 'Recent requests' });
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent(
      'GET /device/resources/items404 device_resource_unknown10.0.0.5',
    );
  });

  it('every request it can send, each sent from here as the device with its answer below it', async () => {
    const { calls } = show(
      <DeviceDetail />,
      {
        [`${API}/3`]: { body: detail },
        [`POST ${API}/3/try`]: {
          body: { answer: { event_id: 'try', answer: { result: 'ok', echo: { n: 1 } } } },
        },
      },
      route,
    );

    const ping = await screen.findByRole('group', { name: 'Report “ping”' });
    expect(ping).toHaveTextContent('POST /device/events');
    expect(ping).toHaveTextContent('Answered with what it was sent.');
    const data = within(ping).getByRole('textbox', { name: 'Data' });
    await userEvent.clear(data);
    await userEvent.type(data, '{{"n": 1}');
    await userEvent.click(within(ping).getByRole('button', { name: 'Send Report “ping”' }));

    expect(await within(ping).findByLabelText('Answer')).toHaveTextContent('"result": "ok"');
    const sent = calls.filter((call) => call.method === 'POST').at(-1);
    expect(await sent?.clone().json()).toEqual({ what: 'report', name: 'ping', data: { n: 1 } });
  });

  it('lists the sign of life and what its type offers, each with its command', async () => {
    show(<DeviceDetail />, { [`${API}/3`]: { body: detail } }, route);

    const hello = await screen.findByRole('group', { name: 'Sign of life' });
    expect(hello).toHaveTextContent('GET /device');
    expect(hello).toHaveTextContent(`Authorization: Bearer <key>`);
    expect(hello).toHaveTextContent(SERVER);
    const fetch = screen.getByRole('group', { name: 'Fetch “hello”' });
    expect(fetch).toHaveTextContent(`${SERVER}/resources/hello`);
    expect(within(fetch).getByRole('button', { name: 'Send Fetch “hello”' })).toBeInTheDocument();
  });

  it('a report that would really happen is not sent from here', async () => {
    show(<DeviceDetail />, { [`${API}/3`]: { body: detail } }, route);

    const sale = await screen.findByRole('group', { name: 'Report “sale”' });
    expect(within(sale).queryByRole('button')).not.toBeInTheDocument();
    expect(sale).toHaveTextContent('Not sent from here');
  });

  it('says when the device itself would be refused', async () => {
    show(
      <DeviceDetail />,
      { [`${API}/3`]: { body: { ...detail, device: { ...reader, enabled: false } } } },
      route,
    );

    expect(
      await screen.findByText(/The device itself would be refused now: it is switched off/),
    ).toBeInTheDocument();
  });

  it('another owner clears only what depends on the owner', async () => {
    const { calls } = show(
      <DeviceDetail />,
      {
        [`${API}/3`]: { body: detail },
        [`PATCH ${API}/3`]: { body: { ...reader, owner: 'rocket', owner_label: 'Rocket' } },
      },
      route,
    );

    const settings = await screen.findByRole('region', { name: 'Settings' });
    await userEvent.click(within(settings).getByRole('combobox', { name: 'Owner' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Rocket' }));
    await userEvent.click(within(settings).getByRole('button', { name: 'Save' }));

    await vi.waitFor(() => {
      expect(calls.some((call) => call.method === 'PATCH')).toBe(true);
    });
    const sent = calls.filter((call) => call.method === 'PATCH').at(-1);
    expect(await sent?.clone().json()).toMatchObject({ owner: 'rocket', settings: { greeting: 'Hello' } });
  });

  it('a sales point sells an item chosen from the price lists, under its seller', async () => {
    const salesPoint: Schemas['DeviceTypeOut'] = {
      slug: 'sales_point',
      label: 'Sales point',
      description: 'A card reader that sells one item.',
      settings: [
        {
          name: 'item',
          label: 'Item',
          kind: 'choice',
          default: null,
          help: '',
          max_length: 200,
          minimum: null,
          maximum: null,
          choices: [
            { value: 4, label: 'Beer, €1.50', group: 'Joanneum Aeronautics', owner: 'association' },
            { value: 9, label: 'Club-Mate, €2.00', group: 'Rocket', owner: 'rocket' },
          ],
        },
      ],
      resources: [],
      reports: [{ name: 'tap', description: 'A card tapped.', tryable: false }],
    };
    const fridge = { ...reader, type: 'sales_point', type_label: 'Sales point', settings: { item: 4 } };
    show(
      <DeviceDetail />,
      {
        [API]: { body: { items: [fridge], types: [testType, salesPoint], owners: OWNERS, server: SERVER } },
        [`${API}/3`]: { body: { ...detail, device: fridge } },
      },
      route,
    );

    expect(await screen.findByDisplayValue('Beer, €1.50')).toBeInTheDocument();
    const report = screen.getByRole('group', { name: 'Report “tap”' });
    expect(report).toHaveTextContent('Not sent from here');
  });
});
