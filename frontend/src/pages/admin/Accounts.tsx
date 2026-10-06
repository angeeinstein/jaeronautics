/**
 * The account list: everybody with an account, members or not, and the old
 * forum's people who have not come back yet. Search, filters, sorting and
 * pages are done by the server and kept in the address (accountFilters.ts);
 * a row opens the account. Data: GET /api/v1/admin/accounts
 * (aeronautics_members/api/admin_accounts.py).
 */
import { Avatar, Button, Card, Chip, Group, Pagination, Select, Stack, Text, TextInput } from '@mantine/core';
import { useDebouncedCallback } from '@mantine/hooks';
import { IconSearch } from '@tabler/icons-react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';

import { api, call, type Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { columnsFor, DataTable, type Sort } from '../../components/DataTable';
import { PageHeader } from '../../components/PageHeader';
import { Pill } from '../../components/Pill';
import { ErrorState, LoadingState } from '../../components/States';
import { formatDate } from '../../lib/format';
import {
  type AccountFilters,
  apiQuery,
  DEFAULT_FILTERS,
  filtersFromParams,
  isFiltered,
  type MembershipFilter,
  MEMBERSHIP_FILTERS,
  paramsFromFilters,
  type SortKey,
} from './accountFilters';
import { ACCOUNT_STATE_PILL, MEMBERSHIP_PILL } from './accountLabels';
import classes from './Accounts.module.css';

type AccountList = Schemas['AccountListOut'];
type Row = Schemas['AccountRow'];

export function accountsQuery(filters: AccountFilters) {
  return {
    queryKey: ['admin', 'accounts', filters] as const,
    queryFn: () => call(api.GET('/api/v1/admin/accounts', { params: { query: apiQuery(filters) } })),
  };
}

const MEMBERSHIP_CHIPS: Record<MembershipFilter, string> = {
  all: 'All',
  active: 'Active',
  ending: 'Ending',
  pending: 'Payment pending',
  failed: 'Payment failed',
  ended: 'Ended',
  none: 'No membership',
};

const ACCOUNT_CHOICES = [
  { value: 'all', label: 'Any account' },
  { value: 'active', label: 'Can sign in' },
  { value: 'no_sign_in', label: 'No sign-in yet' },
  { value: 'disabled', label: 'Deactivated' },
  { value: 'erased', label: 'Erased' },
];

const KIND_CHOICES = [
  { value: 'all', label: 'Portal and old forum' },
  { value: 'portal', label: 'Portal accounts' },
  { value: 'archived', label: 'Old forum, not back yet' },
];

const none = (
  <Text span c="dimmed">
    –
  </Text>
);

function initialsOf(row: Row): string {
  const words = (row.name ?? row.email ?? row.old_forum_username ?? '?').split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? [words[0]?.[0], words.at(-1)?.[0]] : [words[0]?.[0]];
  return letters.join('').toUpperCase();
}

function Person({ row }: { row: Row }) {
  const title = row.name ?? row.email ?? row.old_forum_username ?? `Account ${String(row.id)}`;
  const under = row.name ? (row.email ?? row.old_forum_username) : null;
  const state = ACCOUNT_STATE_PILL[row.account_state];
  return (
    <Group gap="sm" wrap="nowrap">
      <Avatar size={32} color="brand" variant="light" aria-hidden>
        {initialsOf(row)}
      </Avatar>
      <Stack gap={2} className={classes.person}>
        <Group gap={6} wrap="nowrap">
          <AppLink to={`/admin/accounts/${String(row.id)}`} className={classes.name}>
            {title}
          </AppLink>
          {row.old_forum === 'unclaimed' ? <Pill tone="info">Old forum</Pill> : null}
          {row.old_forum === 'reconnected' ? <Pill tone="info">Reconnected</Pill> : null}
          {state ? <Pill tone={state.tone}>{state.label}</Pill> : null}
        </Group>
        {under ? (
          <Text size="sm" c="dimmed" className={classes.under}>
            {under}
          </Text>
        ) : null}
      </Stack>
    </Group>
  );
}

const column = columnsFor<Row>();

const columns = column.columns([
  // Accessor columns: the server sorts, but only a column with a value can be
  // sorted by -- the value itself is not used for it.
  column.accessor('name', { id: 'name', header: 'Name', cell: ({ row }) => <Person row={row.original} /> }),
  column.accessor('category', {
    id: 'kind',
    header: 'Kind',
    cell: ({ row: { original: row } }) =>
      row.category_label || row.year_group ? (
        <Text size="sm" className={classes.nowrap}>
          {row.category_label}
          {row.category_label && row.year_group ? ' · ' : ''}
          {row.year_group ? (
            <Text span size="sm" c="dimmed">
              {row.year_group}
            </Text>
          ) : null}
        </Text>
      ) : (
        none
      ),
  }),
  column.accessor('membership', {
    id: 'membership',
    header: 'Membership',
    cell: ({ row }) => {
      const state = MEMBERSHIP_PILL[row.original.membership];
      return state ? <Pill tone={state.tone}>{state.label}</Pill> : none;
    },
  }),
  column.accessor('membership_until', {
    id: 'until',
    header: 'Paid until',
    cell: ({ row }) =>
      row.original.membership_until ? (
        <span className="ja-figures">{formatDate(row.original.membership_until)}</span>
      ) : (
        none
      ),
  }),
  column.accessor('forum_username', {
    id: 'forum',
    header: 'Forum',
    cell: ({ getValue }) => getValue() ?? none,
  }),
  column.display({
    id: 'roles',
    header: 'Roles',
    cell: ({ row }) =>
      row.original.roles.length ? (
        <Group gap={4}>
          {row.original.roles.map((role) => (
            <Pill key={role.slug} tone="neutral">
              {role.label}
            </Pill>
          ))}
        </Group>
      ) : null,
  }),
]);

function roleChoices(list: AccountList | undefined, current: string) {
  const choices = [
    { value: 'all', label: 'Any role' },
    { value: 'staff', label: 'Any admin role' },
    ...(list?.role_choices.map((role) => ({ value: role.slug, label: role.label })) ?? []),
    { value: 'none', label: 'No role' },
  ];
  // Until the list has loaded, a role from the address is still shown as chosen.
  if (!choices.some((choice) => choice.value === current)) choices.push({ value: current, label: current });
  return choices;
}

/** The search field: the list follows what is typed, a moment after typing stops. */
function Search({ value, onSearch }: { value: string; onSearch: (q: string) => void }) {
  const [text, setText] = useState(value);
  const sent = useRef(value);
  const send = useDebouncedCallback((q: string) => {
    sent.current = q.trim();
    onSearch(q.trim());
  }, 300);

  // The address changed by other means (Reset, the back button): show that.
  useEffect(() => {
    if (value !== sent.current) {
      sent.current = value;
      setText(value);
    }
  }, [value]);

  return (
    <TextInput
      className={classes.search}
      leftSection={<IconSearch size={16} aria-hidden />}
      placeholder="Search name, email or forum username"
      aria-label="Search accounts"
      value={text}
      onChange={(event) => {
        setText(event.currentTarget.value);
        send(event.currentTarget.value);
      }}
    />
  );
}

export function Accounts() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => filtersFromParams(params), [params]);
  const list = useQuery({ ...accountsQuery(filters), placeholderData: keepPreviousData });

  const update = (change: Partial<AccountFilters>, { replace = false } = {}) => {
    // Any change but turning the page starts again at the first.
    const next = { ...filters, page: 1, ...change };
    setParams(paramsFromFilters(next), { replace });
  };

  // An address with old parameters or defaults spelt out becomes today's
  // short form; a page past the end becomes the last one.
  const canonical = paramsFromFilters({
    ...filters,
    page: list.data && !list.isPlaceholderData ? list.data.page : filters.page,
  }).toString();
  useEffect(() => {
    if (params.toString() !== canonical) setParams(new URLSearchParams(canonical), { replace: true });
  }, [params, canonical, setParams]);

  const counts = list.data?.membership_counts;
  const sort: Sort<SortKey> = { by: filters.sort, desc: filters.desc };

  return (
    <>
      <PageHeader
        title="Accounts"
        description="Everybody with an account, members or not."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Accounts' }]}
      />
      <Stack gap="md">
        <Group gap="sm" align="flex-end" className={classes.filters}>
          <Search
            value={filters.q}
            onSearch={(q) => {
              update({ q }, { replace: true });
            }}
          />
          <Select
            aria-label="Role"
            className={classes.select}
            data={roleChoices(list.data, filters.role)}
            value={filters.role}
            allowDeselect={false}
            onChange={(role) => {
              update({ role: role ?? 'all' });
            }}
          />
          <Select
            aria-label="Account"
            className={classes.select}
            data={ACCOUNT_CHOICES}
            value={filters.account}
            allowDeselect={false}
            onChange={(account) => {
              update({ account: (account ?? 'all') as AccountFilters['account'] });
            }}
          />
          <Select
            aria-label="Portal or old forum"
            className={classes.select}
            data={KIND_CHOICES}
            value={filters.kind}
            allowDeselect={false}
            onChange={(kind) => {
              update({ kind: (kind ?? 'all') as AccountFilters['kind'] });
            }}
          />
          {isFiltered(filters) ? (
            <Button
              variant="subtle"
              onClick={() => {
                update({ ...DEFAULT_FILTERS, sort: filters.sort, desc: filters.desc });
              }}
            >
              Reset
            </Button>
          ) : null}
        </Group>

        <Chip.Group
          value={filters.membership}
          onChange={(membership) => {
            update({ membership });
          }}
        >
          <Group gap={6} role="radiogroup" aria-label="Membership">
            {MEMBERSHIP_FILTERS.map((value) => (
              <Chip key={value} value={value} type="radio" size="xs" variant="light">
                {MEMBERSHIP_CHIPS[value]}
                {counts ? (
                  <>
                    {' '}
                    <span className={`${classes.count} ja-figures`}>{counts[value]}</span>
                  </>
                ) : null}
              </Chip>
            ))}
          </Group>
        </Chip.Group>

        {list.isPending ? (
          <LoadingState />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : (
          <>
            <Card padding={0} className={classes.card}>
              <DataTable
                label="Accounts"
                columns={columns}
                data={list.data.items}
                rowId={(row) => String(row.id)}
                rowHref={(row) => `/admin/accounts/${String(row.id)}`}
                sort={sort}
                onSortChange={(next) => {
                  update({ sort: next.by, desc: next.desc });
                }}
                busy={list.isPlaceholderData}
                empty={isFiltered(filters) ? 'No accounts match these filters.' : 'No accounts yet.'}
                minWidth={860}
              />
            </Card>
            <Group justify="space-between" gap="sm">
              <Text size="sm" c="dimmed" role="status">
                {list.data.total === 1 ? '1 account' : `${String(list.data.total)} accounts`}
              </Text>
              {list.data.pages > 1 ? (
                <Pagination
                  total={list.data.pages}
                  value={list.data.page}
                  onChange={(page) => {
                    update({ page });
                  }}
                  size="sm"
                  getControlProps={(control) => ({
                    'aria-label': control === 'next' ? 'Next page' : 'Previous page',
                  })}
                  getItemProps={(page) => ({ 'aria-label': `Page ${String(page)}` })}
                  withEdges={false}
                />
              ) : null}
            </Group>
          </>
        )}
      </Stack>
    </>
  );
}
