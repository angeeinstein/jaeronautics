/**
 * Rendering a piece of the app for a test: the providers as in the app (no
 * transitions, no portals), a router at ``route``, and ``fetch`` answering
 * from ``answers`` -- a path, or "METHOD path", to a status and body.
 */
import { QueryClient } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { vi } from 'vitest';

import { resetCsrfToken } from '../api/client';
import { Providers } from '../app/Providers';

export type Answers = Record<string, { status?: number; body?: unknown } | undefined>;

export function mockFetch(answers: Answers) {
  const calls: Request[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const request =
      input instanceof Request ? input : new Request(new URL(String(input), 'http://portal.test'), init);
    calls.push(request);
    const path = new URL(request.url, 'http://portal.test').pathname;
    const answer = answers[`${request.method} ${path}`] ?? answers[path];
    if (!answer) return Promise.reject(new TypeError(`No answer for ${request.method} ${path}`));
    return Promise.resolve(
      new Response(answer.body === undefined ? null : JSON.stringify(answer.body), {
        status: answer.status ?? 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  });
  vi.stubGlobal('fetch', fetchMock);
  resetCsrfToken();
  return { fetchMock, calls };
}

export function renderPage(
  element: ReactNode,
  { route = '/', path = '*' }: { route?: string; path?: string } = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path, element }], { initialEntries: [route] });
  return render(
    <Providers client={client} env="test">
      <RouterProvider router={router} />
    </Providers>,
  );
}
