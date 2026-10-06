/**
 * The account list's filters, sort and page live in its address, so a
 * filtered list can be bookmarked, shared, reloaded and gone back to.
 *
 * Only what differs from the default is written into the address. Links made
 * for the old page (``?active=active``, ``?membership_status=...``,
 * ``?role=role:admin``) are read and turned into today's filters.
 */
import type { Schemas } from '../../api/client';

type Query = Schemas['AccountListQuery'];

export type MembershipFilter = Query['membership'];
export type AccountFilter = Query['account'];
export type KindFilter = Query['kind'];
export type SortKey = Query['sort'];

export interface AccountFilters {
  q: string;
  membership: MembershipFilter;
  /** "all", "staff" (any admin role), "none" (no role) or a role's slug. */
  role: string;
  account: AccountFilter;
  kind: KindFilter;
  sort: SortKey;
  desc: boolean;
  page: number;
}

export const DEFAULT_FILTERS: AccountFilters = {
  q: '',
  membership: 'all',
  role: 'all',
  account: 'all',
  kind: 'all',
  sort: 'name',
  desc: false,
  page: 1,
};

export const MEMBERSHIP_FILTERS: readonly MembershipFilter[] = [
  'all',
  'active',
  'ending',
  'pending',
  'failed',
  'ended',
  'none',
];
export const ACCOUNT_FILTERS: readonly AccountFilter[] = [
  'all',
  'active',
  'no_sign_in',
  'disabled',
  'erased',
];
export const KIND_FILTERS: readonly KindFilter[] = ['all', 'portal', 'archived'];
export const SORT_KEYS: readonly SortKey[] = ['name', 'kind', 'membership', 'until', 'forum'];

function oneOf<T extends string>(allowed: readonly T[], value: string | null, fallback: T): T {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

// The old page's parameters, and what each meant in today's terms.
const OLD_MEMBERSHIP_STATUS: Record<string, MembershipFilter> = {
  none: 'none',
  pending_checkout: 'pending',
  paid: 'active',
  free_period: 'active',
  cancel_scheduled: 'ending',
  failed: 'failed',
  inactive: 'ended',
};
const OLD_SORT: Record<string, SortKey> = {
  member: 'name',
  email: 'name',
  account: 'name',
  subscription: 'membership',
  active: 'membership',
};

export function filtersFromParams(params: URLSearchParams): AccountFilters {
  let membership = oneOf(MEMBERSHIP_FILTERS, params.get('membership'), 'all');
  if (!params.has('membership')) {
    const old = params.get('membership_status');
    const active = params.get('active');
    if (old !== null && old in OLD_MEMBERSHIP_STATUS) membership = OLD_MEMBERSHIP_STATUS[old] ?? 'all';
    else if (active === 'active') membership = 'active';
    else if (active === 'inactive') membership = 'ended';
  }

  let role = params.get('role')?.trim() ?? 'all';
  if (role.startsWith('role:')) role = role.slice('role:'.length);
  else if (role === 'member') role = 'none';
  else if (role === 'no_membership') {
    role = 'all';
    if (membership === 'all') membership = 'none';
  }
  if (role === '') role = 'all';

  const sortParam = params.get('sort');
  const sort = oneOf(SORT_KEYS, sortParam !== null ? (OLD_SORT[sortParam] ?? sortParam) : null, 'name');
  const page = Number.parseInt(params.get('page') ?? '1', 10);

  return {
    q: (params.get('q') ?? '').trim(),
    membership,
    role,
    account: oneOf(ACCOUNT_FILTERS, params.get('account'), 'all'),
    kind: oneOf(KIND_FILTERS, params.get('kind'), 'all'),
    sort,
    desc: params.get('dir') === 'desc',
    page: Number.isFinite(page) && page > 1 ? page : 1,
  };
}

export function paramsFromFilters(filters: AccountFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q) params.set('q', filters.q);
  if (filters.membership !== 'all') params.set('membership', filters.membership);
  if (filters.role !== 'all') params.set('role', filters.role);
  if (filters.account !== 'all') params.set('account', filters.account);
  if (filters.kind !== 'all') params.set('kind', filters.kind);
  if (filters.sort !== DEFAULT_FILTERS.sort || filters.desc) {
    params.set('sort', filters.sort);
    params.set('dir', filters.desc ? 'desc' : 'asc');
  }
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

/** The API's query for these filters. */
export function apiQuery(filters: AccountFilters): Query {
  return {
    q: filters.q,
    membership: filters.membership,
    role: filters.role,
    account: filters.account,
    kind: filters.kind,
    sort: filters.sort,
    dir: filters.desc ? 'desc' : 'asc',
    page: filters.page,
  };
}

/** Whether anything narrows the list (the sort and the page do not). */
export function isFiltered(filters: AccountFilters): boolean {
  return (
    filters.q !== '' ||
    filters.membership !== 'all' ||
    filters.role !== 'all' ||
    filters.account !== 'all' ||
    filters.kind !== 'all'
  );
}
