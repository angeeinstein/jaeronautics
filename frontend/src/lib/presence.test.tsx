import { act, fireEvent, render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { mockFetch } from '../test/render';
import { EVERY_MS, IDLE_AFTER_MS, pagePattern, presenceTab, resetPresence, usePresence } from './presence';

function Page() {
  usePresence();
  return <input aria-label="Name" />;
}

function show(route: string) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: false, csrf_token: 'token' } },
    'POST /api/v1/presence': { status: 204 },
  });
  const router = createMemoryRouter([{ path: '*', element: <Page /> }], { initialEntries: [route] });
  render(<RouterProvider router={router} />);
  const sent = async () => {
    await act(async () => {
      await Promise.resolve();
    });
    const posts = fetched.calls.filter((call) => call.method === 'POST');
    return Promise.all(posts.map((call) => call.clone().json() as Promise<Record<string, unknown>>));
  };
  return { router, sent };
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  resetPresence();
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('presence', () => {
  it('names a page by its pattern, never by what is in the address', () => {
    expect(pagePattern('/teams/flight-team/manage/prices')).toBe('/teams/:slug/manage/prices');
    expect(pagePattern('/reset-password/secret-token')).toBe('/reset-password/:token');
    expect(pagePattern('/admin/accounts/42')).toBe('/admin/accounts/:userId');
    expect(pagePattern('/no/such/page')).toBe('other');
  });

  it('keeps one id per tab', () => {
    const first = presenceTab();
    resetPresence();
    expect(presenceTab()).toBe(first);
  });

  it('says nothing until somebody does something', async () => {
    const { sent } = show('/join');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVERY_MS * 2);
    });
    expect(await sent()).toEqual([]);
  });

  it('says so on a click, with the page and whether somebody typed', async () => {
    const { sent } = show('/join');
    fireEvent.input(screen.getByLabelText('Name'), { target: { value: 'A' } });
    await vi.waitFor(async () => {
      expect(await sent()).toEqual([{ tab: presenceTab(), page: '/join', typed: true }]);
    });
  });

  it('not more than every half minute, and not once somebody has gone', async () => {
    const { sent } = show('/join');
    fireEvent.pointerDown(window);
    fireEvent.pointerDown(window);
    await vi.waitFor(async () => {
      expect(await sent()).toHaveLength(1);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVERY_MS + 1000);
    });
    expect(await sent()).toHaveLength(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IDLE_AFTER_MS);
    });
    const once = (await sent()).length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVERY_MS * 4);
    });
    expect(await sent()).toHaveLength(once);
  });

  it('says soon when somebody starts typing, between the half minutes', async () => {
    const { sent } = show('/join');
    fireEvent.pointerDown(window);
    await vi.waitFor(async () => {
      expect(await sent()).toHaveLength(1);
    });
    fireEvent.input(screen.getByLabelText('Name'), { target: { value: 'A' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVERY_MS / 3 + 100);
    });
    expect((await sent()).map((body) => body.typed)).toEqual([false, true]);
  });

  it('says so again at once on a new page', async () => {
    const { router, sent } = show('/join');
    fireEvent.pointerDown(window);
    await vi.waitFor(async () => {
      expect(await sent()).toHaveLength(1);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
      await router.navigate('/login');
    });
    await vi.waitFor(async () => {
      expect((await sent()).map((body) => body.page)).toEqual(['/join', '/login']);
    });
  });

  it('says nothing from a tab in the background', async () => {
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    const { sent } = show('/join');
    fireEvent.pointerDown(window);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVERY_MS);
    });
    expect(await sent()).toEqual([]);
  });
});
