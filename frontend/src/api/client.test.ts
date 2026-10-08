import { afterEach, describe, expect, it, vi } from 'vitest';

import { mockFetch } from '../test/render';
import { api, ApiError, call } from './client';

const SESSION = { '/api/v1/session': { body: { signed_in: true, csrf_token: 'token-1' } } };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('calling the API', () => {
  it('answers the data', async () => {
    mockFetch({ '/api/v1/session': { body: { signed_in: false, csrf_token: 't' } } });
    await expect(call(api.GET('/api/v1/session'))).resolves.toEqual({ signed_in: false, csrf_token: 't' });
  });

  it('turns an error answer into an ApiError with its code and fields', async () => {
    mockFetch({
      '/api/v1/me': {
        status: 409,
        body: { error: { code: 'taken', message: 'Taken.', fields: { name: 'In use.' }, details: null } },
      },
    });
    const error = await call(api.GET('/api/v1/me')).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      code: 'taken',
      message: 'Taken.',
      fields: { name: 'In use.' },
    });
  });

  it('says so when the portal cannot be reached', async () => {
    mockFetch({});
    await expect(call(api.GET('/api/v1/me'))).rejects.toMatchObject({ code: 'unreachable', status: 0 });
  });

  it('sends a signed-out visitor to the login page and back', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', {
      origin: window.location.origin,
      pathname: '/admin',
      search: '?x=1',
      hash: '',
      assign,
    });
    mockFetch({
      '/api/v1/me': { status: 401, body: { error: { code: 'not_signed_in', message: 'Sign in.' } } },
    });
    await call(api.GET('/api/v1/me')).catch(() => undefined);
    expect(assign).toHaveBeenCalledWith('/login?next=%2Fadmin%3Fx%3D1');
  });
});

describe('the CSRF token', () => {
  // A change the API has no endpoint for yet: the client's handling is the same for all.
  const change = () => api.POST('/api/v1/me' as never, {} as never);

  it('goes with every change, not with a read', async () => {
    const { calls } = mockFetch({
      ...SESSION,
      'POST /api/v1/me': { status: 204 },
      'GET /api/v1/me': { body: {} },
    });
    await change();
    await api.GET('/api/v1/me');
    const post = calls.find((request) => request.method === 'POST');
    const get = calls.find((request) => request.method === 'GET' && request.url.endsWith('/me'));
    expect(post?.headers.get('X-CSRFToken')).toBe('token-1');
    expect(get?.headers.get('X-CSRFToken')).toBeNull();
  });

  it('is fetched again and the change sent once more when it has expired', async () => {
    let refused = false;
    const { fetchMock } = mockFetch(SESSION);
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const request =
        input instanceof Request ? input : new Request(new URL(String(input), 'http://portal.test'));
      const path = new URL(request.url, 'http://portal.test').pathname;
      if (path === '/api/v1/session') {
        return Promise.resolve(
          Response.json({ signed_in: true, csrf_token: refused ? 'token-2' : 'token-1' }),
        );
      }
      if (!refused) {
        refused = true;
        return Promise.resolve(
          Response.json({ error: { code: 'csrf_failed', message: 'Expired.' } }, { status: 400 }),
        );
      }
      return Promise.resolve(
        new Response(null, { status: request.headers.get('X-CSRFToken') === 'token-2' ? 204 : 400 }),
      );
    });

    const { response } = await change();

    expect(response.status).toBe(204);
  });
});
