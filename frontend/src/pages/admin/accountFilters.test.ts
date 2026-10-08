import { describe, expect, it } from 'vitest';

import { DEFAULT_FILTERS, filtersFromParams, isFiltered, paramsFromFilters } from './accountFilters';

const read = (query: string) => filtersFromParams(new URLSearchParams(query));

describe('the address of the account list', () => {
  it('is empty for the default list', () => {
    expect(read('')).toEqual(DEFAULT_FILTERS);
    expect(paramsFromFilters(DEFAULT_FILTERS).toString()).toBe('');
  });

  it('holds what differs from the default, and reads back the same', () => {
    const filters = {
      ...DEFAULT_FILTERS,
      q: 'anna',
      membership: 'ending' as const,
      role: 'treasurer',
      account: 'disabled' as const,
      kind: 'all' as const,
      sort: 'until' as const,
      desc: true,
      page: 3,
    };
    const query = paramsFromFilters(filters).toString();

    expect(query).toBe(
      'q=anna&membership=ending&role=treasurer&account=disabled&kind=all&sort=until&dir=desc&page=3',
    );
    expect(read(query)).toEqual(filters);
  });

  it('writes the sort only when it is not the default', () => {
    expect(paramsFromFilters({ ...DEFAULT_FILTERS, desc: true }).toString()).toBe('sort=name&dir=desc');
    expect(paramsFromFilters({ ...DEFAULT_FILTERS, sort: 'forum' }).toString()).toBe('sort=forum&dir=asc');
  });

  it('ignores what it does not know', () => {
    expect(read('membership=paid&account=sideways&kind=x&sort=password_hash&page=-4&q=%20%20')).toEqual(
      DEFAULT_FILTERS,
    );
  });

  it.each([
    ['active=active', { membership: 'active' }],
    ['active=inactive', { membership: 'ended' }],
    ['membership_status=cancel_scheduled', { membership: 'ending' }],
    ['membership_status=pending_checkout', { membership: 'pending' }],
    ['membership_status=paid', { membership: 'active' }],
    ['membership_status=inactive', { membership: 'ended' }],
    ['role=role:admin', { role: 'admin' }],
    ['role=member', { role: 'none' }],
    ['role=no_membership', { membership: 'none' }],
    ['sort=member&dir=desc', { sort: 'name', desc: true }],
    ['sort=subscription', { sort: 'membership' }],
  ])('understands the old page’s %s', (query, expected) => {
    expect(read(query)).toEqual({ ...DEFAULT_FILTERS, ...expected });
  });

  it('a new parameter wins over an old one', () => {
    expect(read('membership=failed&active=active').membership).toBe('failed');
  });

  it('knows when the list is narrowed', () => {
    expect(isFiltered(DEFAULT_FILTERS)).toBe(false);
    expect(isFiltered({ ...DEFAULT_FILTERS, sort: 'forum', page: 2 })).toBe(false);
    expect(isFiltered({ ...DEFAULT_FILTERS, kind: 'archived' })).toBe(true);
  });
});
