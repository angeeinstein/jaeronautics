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
  resources: ['hello'],
  reports: ['ping'],
  tryable: ['ping'],
};

const reader: Schemas['DeviceOut'] = {
  id: 3,
  name: 'Fridge reader',
  type: 'test',
  type_label: 'Test',
  place: 'Workshop',
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
    [API]: { body: { items: [reader], types: [testType], server: SERVER } },
    ...answers,
  });
  renderPage(element, route);
  return fetched;
}

describe('devices', () => {
  it('each device with its state', async () => {
    show(<Devices />, {});

    const table = await screen.findByRole('table', { name: 'Devices' });
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('Fridge readerWorkshopTestOnline');
    expect(within(table).getByRole('link', { name: 'Fridge reader' })).toHaveAttribute(
      'href',
      '/admin/settings/devices/3',
    );
  });

  it('adding one shows its key once, to copy or download', async () => {
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
        },
      },
    });

    await userEvent.type(await screen.findByRole('textbox', { name: 'Name' }), 'Door');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));

    expect(await screen.findByTestId('device-key')).toHaveTextContent('jadev_aaaaaaaaaaaa_secret');
    expect(screen.getByRole('button', { name: 'Copy key' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download configuration' })).toBeInTheDocument();
    const sent = calls.filter((call) => call.method === 'POST' && new URL(call.url).pathname === API).at(-1);
    expect(await sent?.clone().json()).toEqual({ name: 'Door', type: 'test', place: '', settings: {} });

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

  it('tries what the device would be answered', async () => {
    show(
      <DeviceDetail />,
      {
        [`${API}/3`]: { body: detail },
        [`POST ${API}/3/try`]: { body: { answer: { result: 'ok', echo: {} } } },
      },
      { route: '/admin/settings/devices/3', path: '/admin/settings/devices/:deviceId' },
    );

    await userEvent.click(await screen.findByRole('button', { name: 'Report “ping”' }));
    expect(await screen.findByLabelText('Answer')).toHaveTextContent('"result": "ok"');
  });

  it('shows how a device talks to the portal, with its address', async () => {
    show(
      <DeviceDetail />,
      { [`${API}/3`]: { body: detail } },
      {
        route: '/admin/settings/devices/3',
        path: '/admin/settings/devices/:deviceId',
      },
    );

    expect(await screen.findByText(/Authorization: Bearer <key>/)).toHaveTextContent(SERVER);
  });
});
