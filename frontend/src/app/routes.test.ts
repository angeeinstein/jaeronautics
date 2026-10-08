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
    ['/admin/teams/new', true],
    ['/admin/teams/rocket', true],
    ['/teams/rocket/manage', true],
    ['/teams/rocket/manage/people/7', true],
    ['/teams/rocket/money', true],
    ['/teams/rocket/money.csv', false],
    ['/admin/money?check=1&since=2026-01-01', true],
    ['/admin/money/rocket', true],
    ['/admin/money/rocket/transfer-code.svg', false],
    ['/admin/logs?user=12', true],
    ['/admin/settings/forum', true],
    ['/admin/settings/mail', true],
    ['/admin/settings/updates', true],
    ['/admin/settings#backup-restore', true],
    ['/admin/settings/backup', true],
    ['/account', true],
    ['/account?rt=123', true],
    ['/account/data-export', false],
    ['/account/create-membership', true],
    ['/', true],
    ['/join', true],
    ['/thank-you?method=invoice&phase=free_period', true],
    ['/cancel', true],
    ['/account/delete/abc.def', true],
    ['/change-password', true],
    ['/login', true],
    ['/legal', true],
    ['/legal/statutes/de/2026-01-01', true],
    ['/legal/statutes/pdf', false],
    ['/legal/statutes/pdf/2026-01-01', false],
    ['/teams/rocket/rules/en/2026-01-01', true],
    ['/teams/rocket/rules/pdf', false],
    ['/login?next=%2Fforum', true],
    ['/forgot-password', true],
    ['/reset-password/abc', true],
    ['/logout', false],
    ['/forum?token=abc', true],
    ['/forum/discourse/connect', false],
    ['/forum/avatar/public/abc', false],
    ['https://example.org/admin', false],
    ['//example.org/admin', false],
  ])('%s is drawn by the app: %s', (href, expected) => {
    expect(isAppPath(href)).toBe(expected);
  });
});
