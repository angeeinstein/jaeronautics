import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { DeviceCrashes } from './DeviceCrashes';

const API = '/api/v1/admin/devices/3/crashes';
const crash: Schemas['DeviceCrashOut'] = {
  id: 7,
  received_at: '2026-10-16T08:12:00Z',
  version: '0.4.2',
  build: '72ce52965e229fed',
  reported_by: '0.4.3',
  task: 'portal',
  pc: '0x4209F87C',
  backtrace: ['0x4209F879', '0x4209FB05'],
  backtrace_corrupted: false,
  reset_reason: 'panic',
  size: 41260,
  has_file: true,
};

function show(answers: Answers) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    ...answers,
  });
  renderPage(<DeviceCrashes deviceId={3} />);
  return fetched;
}

describe('a device’s crashes', () => {
  it('each with the version that crashed, why, the call path and its dump', async () => {
    show({ [API]: { body: { crashes: [crash] } } });

    expect(await screen.findByText('Crashed')).toBeInTheDocument();
    expect(screen.getByText('0.4.2 · task portal · reported by 0.4.3')).toBeInTheDocument();
    expect(screen.getByLabelText('Call path')).toHaveTextContent('0x4209F87C 0x4209F879 0x4209FB05');
    expect(screen.getByRole('link', { name: 'Dump (40 KB)' })).toHaveAttribute(
      'href',
      '/api/v1/admin/devices/3/crashes/7/file',
    );
  });

  it('a build never uploaded, a watchdog, no dump', async () => {
    show({
      [API]: {
        body: {
          crashes: [
            {
              ...crash,
              version: null,
              reported_by: null,
              reset_reason: 'task_watchdog',
              has_file: false,
              backtrace_corrupted: true,
            },
          ],
        },
      },
    });

    expect(await screen.findByText('Task hung')).toBeInTheDocument();
    expect(screen.getByText('Build 72ce52965e229fed · task portal')).toBeInTheDocument();
    expect(screen.getByText('The call path could not be read completely.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Dump/ })).not.toBeInTheDocument();
  });

  it('deleted on a deliberate yes', async () => {
    const { calls } = show({
      [`GET ${API}`]: { body: { crashes: [crash] } },
      [`DELETE ${API}/7`]: { body: { crashes: [] } },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, delete' }));

    expect(await screen.findByText('No crashes reported.')).toBeInTheDocument();
    expect(calls.some((call) => call.method === 'DELETE')).toBe(true);
  });

  it('none yet', async () => {
    show({ [API]: { body: { crashes: [] } } });
    const panel = await screen.findByRole('region', { name: 'Crashes' });
    expect(within(panel).getByText('No crashes reported.')).toBeInTheDocument();
  });
});
