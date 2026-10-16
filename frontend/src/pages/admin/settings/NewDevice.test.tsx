import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { Firmware } from './Firmware';
import { chipKey, chipLabel, NewDevice } from './NewDevice';

// The board, as esptool-js would find it: an ESP32-S3 unless a test says otherwise.
const board = vi.hoisted(() => ({
  chip: 'ESP32-S3',
  flashed: [] as { eraseAll: boolean; address: number; size: number; md5: string }[],
  reset: vi.fn(),
  disconnected: vi.fn(),
  fail: null as Error | null,
}));

vi.mock('esptool-js', () => ({
  Transport: class {
    disconnect() {
      board.disconnected();
      return Promise.resolve();
    }
  },
  ESPLoader: class {
    chip = { CHIP_NAME: board.chip, readMac: () => Promise.resolve('98:a3:16:e5:06:3c') };
    main() {
      return board.fail ? Promise.reject(board.fail) : Promise.resolve(board.chip);
    }
    writeFlash(options: {
      fileArray: { data: Uint8Array; address: number }[];
      eraseAll: boolean;
      reportProgress: (file: number, written: number, total: number) => void;
      calculateMD5Hash: (image: Uint8Array) => string;
    }) {
      const [file] = options.fileArray;
      if (!file) throw new Error('nothing to write');
      options.reportProgress(0, file.data.length, file.data.length);
      board.flashed.push({
        eraseAll: options.eraseAll,
        address: file.address,
        size: file.data.length,
        md5: options.calculateMD5Hash(file.data),
      });
      return Promise.resolve();
    }
    after() {
      board.reset();
      return Promise.resolve();
    }
  },
}));

const API = '/api/v1/admin/devices/firmware/factory';
const image: Schemas['FactoryImageOut'] = {
  id: 4,
  chip: 'esp32s3',
  version: '1.2.0',
  project: 'jae-terminal',
  built: 'Oct 14 2026 09:12:44',
  idf_version: 'v5.3.1',
  sha256: 'ab'.repeat(32),
  size: 1_310_720,
  file_name: 'jae-terminal-factory.bin',
  uploaded_at: '2026-10-16T08:00:00Z',
  uploaded_by: 'admin@example.org',
};
const factory: Schemas['FactoryImagesOut'] = {
  images: [image],
  project: 'jae-terminal',
  max_size: 16 * 1024 * 1024,
};

function show(answers: Answers = {}, element: React.ReactNode = <NewDevice />, route = '/') {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    [API]: { body: factory },
    // The image, as the page fetches it for the board ("abc", JSON-quoted by the mock: 5 bytes).
    [`${API}/4/file`]: { body: 'abc' },
    ...answers,
  });
  renderPage(element, { route, path: '*' });
  return fetched;
}

beforeEach(() => {
  board.chip = 'ESP32-S3';
  board.flashed = [];
  board.fail = null;
  Object.defineProperty(navigator, 'serial', {
    value: { requestPort: () => Promise.resolve({}) },
    configurable: true,
  });
});

afterEach(() => {
  Reflect.deleteProperty(navigator, 'serial');
  vi.clearAllMocks();
});

describe('chip names', () => {
  it('esptool-js’s and the portal’s meet', () => {
    expect(chipKey('ESP32-S3')).toBe('esp32s3');
    expect(chipKey('ESP32')).toBe('esp32');
    expect(chipLabel('esp32s3')).toBe('ESP32-S3');
    expect(chipLabel('esp32')).toBe('ESP32');
    expect(chipLabel('esp32c61')).toBe('ESP32-C61');
  });
});

describe('a new device', () => {
  it('is a tab of the Firmware page', async () => {
    show(
      {
        '/api/v1/admin/devices/firmware': {
          body: { releases: [], chosen_id: null, project: null, max_size: 1, devices: [] },
        },
      },
      <Firmware />,
      '/#new',
    );

    expect(await screen.findByRole('tab', { name: 'New device', selected: true })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Connect and flash' })).toBeInTheDocument();
  });

  it('the factory image is listed by its chip', async () => {
    show();

    const table = await screen.findByRole('table', { name: 'Factory images' });
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('ESP32-S31.2.0');
  });

  it('a board flashed: everything erased first, the image from 0, checked by its MD5, restarted', async () => {
    show();

    await userEvent.click(await screen.findByRole('button', { name: 'Connect and flash' }));

    expect(await screen.findByText('Flashed and checked')).toBeInTheDocument();
    expect(board.flashed).toEqual([
      // "abc" with its quotes, and its MD5.
      { eraseAll: true, address: 0, size: 5, md5: 'ebd9f4c7b06cb0aaf5d13d80e49d8b90' },
    ]);
    expect(board.reset).toHaveBeenCalled();
    expect(board.disconnected).toHaveBeenCalled();
    expect(screen.getByText('ESP32-S3 · 98:A3:16:E5:06:3C')).toBeInTheDocument();
    // Pairing is a step of its own.
    expect(screen.getByRole('link', { name: 'pair it' })).toHaveAttribute(
      'href',
      '/admin/settings/devices/pair',
    );
  });

  it('without erasing, when asked', async () => {
    show();

    await userEvent.click(await screen.findByRole('checkbox', { name: /Erase everything first/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Connect and flash' }));

    await screen.findByText('Flashed and checked');
    expect(board.flashed[0]?.eraseAll).toBe(false);
  });

  it('a board of another chip is not written to', async () => {
    board.chip = 'ESP32-C3';
    show();

    await userEvent.click(await screen.findByRole('button', { name: 'Connect and flash' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This board is an ESP32-C3; there is no factory image for it. Images: ESP32-S3.',
    );
    expect(board.flashed).toEqual([]);
    expect(board.disconnected).toHaveBeenCalled();
  });

  it('a board that does not answer: how to get it to', async () => {
    board.fail = new Error('Failed to connect with the device');
    show();

    await userEvent.click(await screen.findByRole('button', { name: 'Connect and flash' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Hold its BOOT button while plugging it in');
  });

  it('a browser without Web Serial is told so', async () => {
    Reflect.deleteProperty(navigator, 'serial');
    show();

    expect(await screen.findByText(/This browser cannot reach a board over USB/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Connect and flash' })).not.toBeInTheDocument();
  });
});
