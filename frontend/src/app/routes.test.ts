import type { RouteObject } from 'react-router';
import { describe, expect, it } from 'vitest';

import { APP_PATHS, isAppPath } from './paths';
import { routes } from './routes';

function routePaths(list: RouteObject[], parent = ''): string[] {
  return list.flatMap((route) => {
    const own = route.path ? (route.path.startsWith('/') ? route.path : `${parent}/${route.path}`) : parent;
    const here =
      route.path && route.path !== '*' && !route.children?.some((child) => child.index) ? [own] : [];
    const index = route.children?.some((child) => child.index) ? [own] : [];
    return [...here, ...index, ...routePaths(route.children ?? [], own)];
  });
}

describe('the pages the app draws', () => {
  it('are exactly the addresses Flask hands it (paths.json)', () => {
    expect(new Set(routePaths(routes))).toEqual(new Set(APP_PATHS));
  });

  it.each([
    ['/admin', true],
    ['/admin?tab=x', true],
    ['/admin#top', true],
    ['/admin/accounts', true],
    ['/admin/accounts?membership=active', true],
    ['/admin/accounts/12', true],
    ['/admin/accounts/12/data-export', false],
    ['/admin/reviews#review-queue', true],
    ['/account', false],
    ['https://example.org/admin', false],
    ['//example.org/admin', false],
  ])('%s is drawn by the app: %s', (href, expected) => {
    expect(isAppPath(href)).toBe(expected);
  });
});
