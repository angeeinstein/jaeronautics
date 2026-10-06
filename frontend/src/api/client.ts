/**
 * Calling the portal's API (aeronautics_members/api/), typed end to end:
 * paths, parameters, bodies and answers come from schema.d.ts, which is
 * generated from the API's own description. A field renamed on the server is
 * a type error here.
 *
 *   const me = await call(api.GET('/api/v1/me'));
 *
 * Changes carry the CSRF token in X-CSRFToken. The token expires after an
 * hour; a change refused for that is retried once with a fresh one, so a page
 * left open does not lose what was typed. Signed out in the meantime, the
 * browser goes to the login page and comes back here afterwards.
 */
import createClient, { type Middleware } from 'openapi-fetch';

import type { components, paths } from './schema';

export type Schemas = components['schemas'];

/** An error answer of the API, or a failure to reach it at all. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: Record<string, string>;
  readonly details: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    fields?: Record<string, string> | null,
    details?: Record<string, unknown> | null,
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.fields = fields ?? {};
    this.details = details ?? {};
  }
}

const SESSION_PATH = '/api/v1/session';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

let csrfToken: Promise<string> | null = null;

async function fetchCsrfToken(): Promise<string> {
  const response = await fetch(new URL(SESSION_PATH, window.location.origin), {
    credentials: 'same-origin',
    cache: 'no-store',
  });
  if (!response.ok)
    throw new ApiError(response.status, 'session_unavailable', 'The portal could not be reached.');
  const body = (await response.json()) as Schemas['SessionOut'];
  return body.csrf_token;
}

function currentCsrfToken(): Promise<string> {
  csrfToken ??= fetchCsrfToken().catch((error: unknown) => {
    csrfToken = null;
    throw error;
  });
  return csrfToken;
}

/** Where a signed-out visitor goes, coming back to this page afterwards. */
export function loginUrl(): string {
  const here = window.location.pathname + window.location.search + window.location.hash;
  return `/login?next=${encodeURIComponent(here)}`;
}

/** Kept aside per request, so a change refused for an expired token can be sent again. */
const resendable = new Map<string, Request>();

const middleware: Middleware = {
  async onRequest({ request, id }) {
    if (SAFE_METHODS.has(request.method)) return request;
    request.headers.set('X-CSRFToken', await currentCsrfToken());
    resendable.set(id, request.clone());
    return request;
  },
  async onResponse({ response, id }) {
    const original = resendable.get(id);
    resendable.delete(id);
    if (response.status === 401) {
      window.location.assign(loginUrl());
      return response;
    }
    if (response.status === 400 && original) {
      const body = (await response
        .clone()
        .json()
        .catch(() => null)) as Schemas['ErrorOut'] | null;
      if (body?.error.code === 'csrf_failed') {
        csrfToken = null;
        original.headers.set('X-CSRFToken', await currentCsrfToken());
        return fetch(original);
      }
    }
    return response;
  },
};

export const api = createClient<paths>({
  // The page's own origin: the API is always on the same site.
  baseUrl: window.location.origin,
  credentials: 'same-origin',
  cache: 'no-store',
  // Looked up on every call rather than once, so it can be swapped (tests).
  fetch: (request) => globalThis.fetch(request),
});
api.use(middleware);

interface Answer<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** The answer's data, or an ApiError with the API's code, message and fields. */
export async function call<T>(request: Promise<Answer<T>>): Promise<T> {
  let answer: Answer<T>;
  try {
    answer = await request;
  } catch {
    throw new ApiError(
      0,
      'unreachable',
      'The portal could not be reached. Check the connection and try again.',
    );
  }
  const { data, error, response } = answer;
  if (response.ok) return data as T;
  const body = error as Schemas['ErrorOut'] | undefined;
  if (body?.error) {
    throw new ApiError(
      response.status,
      body.error.code,
      body.error.message,
      body.error.fields,
      body.error.details,
    );
  }
  throw new ApiError(
    response.status,
    'unexpected',
    'Something went wrong on our side. Please try again later.',
  );
}

/** For tests: forget the token, as a new page load would. */
export function resetCsrfToken(): void {
  csrfToken = null;
}
