import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { DeviceDetail } from './Device';
import { Devices } from './Devices';
import { Firmware } from './Firmware';
import { PairDevice } from './PairDevice';

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
  board: 'jae-terminal-s3',
  supported_types: null,
  backup_code: '482913',
  firmware_wanted: null,
  firmware_state: 'none',
  firmware_release_id: null,
  firmware_failed_detail: null,
  key_hint: 'jadev_3f9a1c2b7d10_…AbCd',
  key_last_used_at: '2026-10-12T07:00:00Z',
  key_revoked_at: null,
  key_created_at: '2026-10-12T06:00:00Z',
  created_at: '2026-10-12T06:00:00Z',
};

const release: Schemas['FirmwareReleaseOut'] = {
  id: 7,
  version: '0.3.0',
  project: 'jae-terminal',
  built: 'Oct 14 2026 09:12:44',
  idf_version: 'v5.3.1',
  chip: 'esp32s3',
  sha256: 'ab'.repeat(32),
  size: 1_468_006,
  file_name: 'jae-terminal.bin',
  notes: 'Faster taps',
  uploaded_at: '2026-10-14T08:00:00Z',
  uploaded_by: 'admin@example.org',
  chosen: false,
  chosen_for: 0,
  running: 0,
};

const firmware: Schemas['FirmwareOut'] = {
  releases: [release],
  chosen_id: null,
  project: 'jae-terminal',
  max_size: 16 * 1024 * 1024,
  devices: [
    {
      id: 3,
      code: 'K7MQ-2X9A',
      label: 'Fridge reader',
      online: true,
      running: '0.2.0',
      wanted: '0.3.0',
      own: false,
      state: 'failed',
      failed_detail: 'Image invalid',
    },
  ],
};

const pairing: Schemas['DevicePairingOut'] = {
  code: 'K7Q-4X9',
  hardware_id: '98:A3:16:E5:06:3C',
  firmware: '0.2.0',
  board: 'jae-terminal-s3',
  supported_types: ['test'],
  address: '10.0.0.9',
  created_at: '2026-10-14T08:00:00Z',
  expires_at: '2026-10-14T08:10:00Z',
};

function show(element: React.ReactNode, answers: Answers, route?: { route: string; path: string }) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    [API]: { body: { items: [reader], pairings: [], types: [testType], owners: OWNERS, server: SERVER } },
    [`${API}/firmware`]: { body: firmware },
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

describe('pairing', () => {
  it('a device waiting is listed, a click from its approval page', async () => {
    show(<Devices />, {
      [API]: {
        body: { items: [reader], pairings: [pairing], types: [testType], owners: OWNERS, server: SERVER },
      },
    });

    const waiting = await screen.findByRole('table', { name: 'Waiting to be paired' });
    expect(within(waiting).getAllByRole('row')[1]).toHaveTextContent('K7Q-4X9');
    expect(within(waiting).getAllByRole('row')[1]).toHaveTextContent('jae-terminal-s3 · 98:A3:16:E5:06:3C');
    expect(within(waiting).getByRole('link', { name: 'Pair' })).toHaveAttribute(
      'href',
      '/admin/settings/devices/pair?code=K7Q-4X9',
    );
    expect(screen.getByRole('link', { name: 'Pair a device' })).toHaveAttribute(
      'href',
      '/admin/settings/devices/pair',
    );
  });

  const pairRoute = {
    route: '/admin/settings/devices/pair?code=K7Q-4X9',
    path: '/admin/settings/devices/pair',
  };

  it('approved as a new device: only a type its firmware can be', async () => {
    const salesPoint = { ...testType, slug: 'sales_point', label: 'Sales point', settings: [] };
    const { calls } = show(
      <PairDevice />,
      {
        [API]: {
          body: {
            items: [reader],
            pairings: [pairing],
            types: [testType, salesPoint],
            owners: OWNERS,
            server: SERVER,
          },
        },
        [`${API}/pairings/K7Q-4X9`]: { body: pairing },
        [`POST ${API}/pairings/K7Q-4X9`]: { status: 201, body: { ...reader, id: 8, label: 'Office' } },
        [`${API}/8`]: { body: { device: { ...reader, id: 8 }, events: [], requests: [], server: SERVER } },
      },
      pairRoute,
    );

    const found = await screen.findByRole('region', { name: 'Device K7Q-4X9' });
    expect(found).toHaveTextContent('Can beTest');
    // Its firmware can only be a test device: the only type, chosen.
    expect(screen.getByRole('combobox', { name: 'Type' })).toHaveValue('Test');
    await userEvent.type(screen.getByRole('textbox', { name: /^Name/ }), 'Office');
    await userEvent.click(screen.getByRole('button', { name: 'Pair' }));

    await vi.waitFor(() => {
      expect(calls.some((call) => call.method === 'POST')).toBe(true);
    });
    const sent = calls.filter((call) => call.method === 'POST').at(-1);
    expect(await sent?.clone().json()).toEqual({
      type: 'test',
      owner: 'association',
      name: 'Office',
      place: '',
      settings: {},
    });
  });

  it('as the new board of a device', async () => {
    const { calls } = show(
      <PairDevice />,
      {
        [`${API}/pairings/K7Q-4X9`]: { body: pairing },
        [`POST ${API}/pairings/K7Q-4X9`]: { status: 201, body: reader },
        [`${API}/3`]: { body: { device: reader, events: [], requests: [], server: SERVER } },
      },
      pairRoute,
    );

    await userEvent.click(await screen.findByRole('radio', { name: 'The board of a device' }));
    await userEvent.click(screen.getByRole('combobox', { name: 'Device' }));
    await userEvent.click(await screen.findByRole('option', { name: /Fridge reader/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Pair' }));

    await vi.waitFor(() => {
      expect(calls.some((call) => call.method === 'POST')).toBe(true);
    });
    const sent = calls.filter((call) => call.method === 'POST').at(-1);
    expect(await sent?.clone().json()).toEqual({ replaces: 3 });
  });

  it('a code no device waits with', async () => {
    show(
      <PairDevice />,
      {
        [`${API}/pairings/K7Q-4X9`]: {
          status: 404,
          body: { error: { code: 'pairing_unknown', message: 'No device is waiting with this code.' } },
        },
      },
      pairRoute,
    );

    expect(await screen.findByText(/No device is waiting with this code/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Enter another code' }));
    expect(await screen.findByRole('textbox', { name: 'Code' })).toBeInTheDocument();
  });
});

describe('firmware', () => {
  it('each version, read from its file, and where each device stands', async () => {
    show(<Firmware />, {});

    const table = await screen.findByRole('table', { name: 'Firmware' });
    const row = within(table).getAllByRole('row')[1];
    expect(row).toHaveTextContent('0.3.0');
    expect(row).toHaveTextContent('Faster taps');
    expect(row).toHaveTextContent('esp32s3 · v5.3.1 · 1.4 MB');
    const devices = screen.getByRole('table', { name: "Devices' firmware" });
    expect(within(devices).getAllByRole('row')[1]).toHaveTextContent(
      'Fridge reader0.2.00.3.0Update failedImage invalid',
    );
    expect(screen.getByText(/of “jae-terminal”/)).toBeInTheDocument();
  });

  it('the device protocol, for whoever writes the firmware, is downloaded from here', async () => {
    show(<Firmware />, {});

    const link = await screen.findByRole('link', { name: 'Device protocol' });
    expect(link).toHaveAttribute('href', '/api/v1/admin/devices/protocol');
    expect(link).toHaveAttribute('download');
  });

  it('installed on every device', async () => {
    const { calls } = show(<Firmware />, {
      [`PUT ${API}/firmware/chosen`]: {
        body: { ...firmware, chosen_id: 7, releases: [{ ...release, chosen: true }] },
      },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Install on every device' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, install 0.3.0' }));

    expect(await screen.findByText('Every device')).toBeInTheDocument();
    const sent = calls.filter((call) => call.method === 'PUT').at(-1);
    expect(await sent?.clone().json()).toEqual({ release_id: 7 });
  });

  it('a file uploaded, and what is wrong with one said under the field', async () => {
    const { calls } = show(<Firmware />, {
      [`POST ${API}/firmware`]: {
        status: 400,
        body: {
          error: {
            code: 'firmware_invalid',
            message: 'This firmware file is damaged: its checksum does not match.',
            fields: { file: 'This firmware file is damaged: its checksum does not match.' },
          },
        },
      },
    });

    await screen.findByRole('button', { name: 'Firmware file' });
    // The field is a button; the file goes to the input beside it.
    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
    await userEvent.upload(input ?? document.body, new File([new Uint8Array([0xe9, 1, 2])], 'app.bin'));
    await userEvent.click(screen.getByRole('button', { name: 'Upload' }));

    expect(
      await screen.findByText('This firmware file is damaged: its checksum does not match.'),
    ).toBeInTheDocument();
    const sent = calls.filter((call) => call.method === 'POST').at(-1);
    // Read as sent: parsing it back with formData() trips over two File classes on Node 24.
    expect(sent?.headers.get('content-type')).toMatch(/^multipart\/form-data/);
    expect(await sent?.clone().text()).toContain('name="file"');
  });
});

/** The device's requests are folded away until asked for. */
/** A device's requests and events are on its second tab. */
async function openActivity() {
  await userEvent.click(await screen.findByRole('tab', { name: 'Activity' }));
}

async function openRequests() {
  await openActivity();
  const panel = await screen.findByRole('region', { name: 'What the device can send' });
  await userEvent.click(within(panel).getByRole('button', { name: 'Show' }));
}

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

  it('a paired device has no key panel: its key was handed to it, and nobody saw it', async () => {
    show(
      <DeviceDetail />,
      { [`${API}/3`]: { body: { ...detail, device: { ...reader, paired: true } } } },
      route,
    );

    expect(await screen.findByRole('region', { name: 'State' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Key' })).not.toBeInTheDocument();
    await openActivity();
    expect(screen.getByRole('region', { name: 'What the device can send' })).toBeInTheDocument();
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
    await openActivity();

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

    await openRequests();
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

    await openRequests();
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

    await openRequests();
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

    await openRequests();
    expect(
      await screen.findByText(/The device itself would be refused now: it is switched off/),
    ).toBeInTheDocument();
  });

  it('its firmware: failed, tried again, or one of its own', async () => {
    const failed = {
      ...reader,
      firmware_wanted: '0.3.0',
      firmware_state: 'failed' as const,
      firmware_failed_detail: 'Image invalid',
    };
    const { calls } = show(
      <DeviceDetail />,
      {
        [`${API}/3`]: { body: { ...detail, device: failed } },
        [`POST ${API}/3/firmware/retry`]: { body: { ...failed, firmware_state: 'waiting' } },
      },
      route,
    );

    const card = await screen.findByRole('region', { name: 'Firmware' });
    expect(card).toHaveTextContent('Update failed');
    expect(card).toHaveTextContent('Runs 0.1.0; should run 0.3.0.');
    expect(card).toHaveTextContent('Image invalid');
    await userEvent.click(within(card).getByRole('button', { name: 'Try again' }));
    await vi.waitFor(() => {
      expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/firmware/retry'))).toBe(true);
    });
  });

  it('its backup code, and what it can be', async () => {
    show(
      <DeviceDetail />,
      { [`${API}/3`]: { body: { ...detail, device: { ...reader, supported_types: ['test'] } } } },
      route,
    );

    const backup = await screen.findByRole('region', { name: 'Backup code' });
    expect(within(backup).getByLabelText('Backup code')).toHaveTextContent('482913');
    const state = screen.getByRole('region', { name: 'State' });
    expect(state).toHaveTextContent('jae-terminal-s3 · 24:6F:28:AA:BB:CC');
    expect(state).toHaveTextContent('Can beTest');
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

  it("a sales point's menu is not on its page: it is chosen on its owner's Sales page", async () => {
    const salesPoint: Schemas['DeviceTypeOut'] = {
      slug: 'sales_point',
      label: 'Sales point',
      description: 'A card reader with a menu of up to four items.',
      settings: [
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
          choices: [
            { value: 4, label: 'Beer, €1.50', group: 'Joanneum Aeronautics', owner: 'association' },
            { value: 5, label: 'Coffee, €0.80', group: 'Joanneum Aeronautics', owner: 'association' },
            { value: 9, label: 'Club-Mate, €2.00', group: 'Rocket', owner: 'rocket' },
          ],
        },
      ],
      resources: [],
      reports: [{ name: 'card', description: 'A card tapped.', tryable: false }],
    };
    const fridge = { ...reader, type: 'sales_point', type_label: 'Sales point', settings: { items: [5, 4] } };
    show(
      <DeviceDetail />,
      {
        [API]: {
          body: {
            items: [fridge],
            pairings: [],
            types: [testType, salesPoint],
            owners: OWNERS,
            server: SERVER,
          },
        },
        [`${API}/3`]: { body: { ...detail, device: fridge } },
      },
      route,
    );

    expect(await screen.findByRole('link', { name: 'Sales page' })).toHaveAttribute('href', '/admin/sales');
    expect(screen.queryByRole('combobox', { name: 'Items' })).not.toBeInTheDocument();
    await openRequests();
    const report = screen.getByRole('group', { name: 'Report “card”' });
    expect(report).toHaveTextContent('Not sent from here');
  });
});
