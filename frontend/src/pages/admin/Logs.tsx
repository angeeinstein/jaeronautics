/**
 * The log (docs/frontend-structure.md, 4): every change recorded, newest
 * first -- who made it, to whom, and what it looked like before and after,
 * folded until asked for. Searched, narrowed to a category, or to everything
 * about one person (?user=, from an account's Activity tab); all of it in the
 * address. Data: GET /api/v1/admin/logs (aeronautics_members/api/admin_logs.py).
 */
import { Button, Card, Group, Pagination, Select, Stack, Table, Text, UnstyledButton } from '@mantine/core';
import { IconChevronDown, IconChevronRight, IconX } from '@tabler/icons-react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useSearchParams } from 'react-router';

import { api, call, type Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { AuditPayload, hasPayload } from '../../components/AuditPayload';
import { PageHeader } from '../../components/PageHeader';
import { SearchField } from '../../components/SearchField';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import { formatDateTime, titleFromCode } from '../../lib/format';
import classes from './Logs.module.css';

type LogsOut = Schemas['LogsOut'];
type Entry = LogsOut['items'][number];
type Person = NonNullable<Entry['actor']>;

interface Filters {
  q: string;
  category: string;
  user: number | null;
  page: number;
}

function filtersFrom(params: URLSearchParams): Filters {
  const user = Number(params.get('user'));
  const page = Number(params.get('page'));
  // The old page's "all" meant no category.
  const category = params.get('category') ?? '';
  return {
    q: params.get('q') ?? '',
    category: category === 'all' ? '' : category,
    user: Number.isInteger(user) && user > 0 ? user : null,
    page: Number.isInteger(page) && page > 1 ? page : 1,
  };
}

function paramsFrom(filters: Filters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q) params.set('q', filters.q);
  if (filters.category) params.set('category', filters.category);
  if (filters.user !== null) params.set('user', String(filters.user));
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

function logsQuery(filters: Filters) {
  return {
    queryKey: ['admin', 'logs', filters] as const,
    queryFn: () =>
      call(
        api.GET('/api/v1/admin/logs', {
          params: {
            query: {
              q: filters.q || undefined,
              category: filters.category || undefined,
              user: filters.user ?? undefined,
              page: filters.page,
            },
          },
        }),
      ),
  };
}

function Who({ person, linked, none }: { person: Person | null; linked: boolean; none: string }) {
  if (person === null) return <Text c="dimmed">{none}</Text>;
  if (linked && person.user_id !== null) {
    return (
      <AppLink to={`/admin/accounts/${String(person.user_id)}`} className={classes.person}>
        {person.email}
      </AppLink>
    );
  }
  return <span className={classes.person}>{person.email}</span>;
}

function Row({ entry, linked }: { entry: Entry; linked: boolean }) {
  const [open, setOpen] = useState(false);
  const details = hasPayload(entry);
  const label = `${titleFromCode(entry.category)} / ${titleFromCode(entry.event_type)}`;
  return (
    <>
      <Table.Tr>
        <Table.Td className={classes.when}>{formatDateTime(entry.at)}</Table.Td>
        <Table.Td>
          {details ? (
            <UnstyledButton
              className={classes.toggle}
              aria-expanded={open}
              onClick={() => {
                setOpen(!open);
              }}
            >
              {open ? <IconChevronDown size={14} aria-hidden /> : <IconChevronRight size={14} aria-hidden />}
              <span>{label}</span>
            </UnstyledButton>
          ) : (
            <span className={classes.plain}>{label}</span>
          )}
        </Table.Td>
        <Table.Td>
          <Who person={entry.actor} linked={linked} none="the system" />
        </Table.Td>
        <Table.Td>
          <Who person={entry.target} linked={linked} none="–" />
        </Table.Td>
      </Table.Tr>
      {open ? (
        <Table.Tr className={classes.payloadRow}>
          <Table.Td colSpan={4}>
            <AuditPayload before={entry.before} after={entry.after} details={entry.details} />
          </Table.Td>
        </Table.Tr>
      ) : null}
    </>
  );
}

export function Logs() {
  const [params, setParams] = useSearchParams();
  const filters = filtersFrom(params);
  const logs = useQuery({ ...logsQuery(filters), placeholderData: keepPreviousData });

  const update = (change: Partial<Filters>, { replace = false } = {}) => {
    // Any change but turning the page starts again at the first.
    setParams(paramsFrom({ ...filters, page: 1, ...change }), { replace });
  };
  const filtered = Boolean(filters.q || filters.category || filters.user !== null);

  return (
    <>
      <PageHeader
        title="Logs"
        description="Every change recorded: who made it, to whom, and what it was before."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Logs' }]}
      />
      <Stack gap="md">
        <Group gap="sm" align="flex-end" className={classes.filters}>
          <SearchField
            label="Search the log"
            placeholder="Search an address, category or event"
            className={classes.search}
            value={filters.q}
            onSearch={(q) => {
              update({ q }, { replace: true });
            }}
          />
          <Select
            aria-label="Category"
            className={classes.select}
            allowDeselect={false}
            data={[
              { value: '', label: 'All categories' },
              ...(logs.data?.categories ?? (filters.category ? [filters.category] : [])).map((category) => ({
                value: category,
                label: titleFromCode(category),
              })),
            ]}
            value={filters.category}
            onChange={(category) => {
              update({ category: category ?? '' });
            }}
          />
          {filtered ? (
            <Button
              variant="default"
              onClick={() => {
                setParams(new URLSearchParams());
              }}
            >
              Reset
            </Button>
          ) : null}
        </Group>
        {logs.data?.about ? (
          <Group gap="xs">
            <Text size="sm">
              Only what is about <span className={classes.person}>{logs.data.about.email}</span>: done to
              them, by them, or to their membership.
            </Text>
            <Button
              size="compact-sm"
              variant="subtle"
              leftSection={<IconX size={14} aria-hidden />}
              onClick={() => {
                update({ user: null });
              }}
            >
              Everybody
            </Button>
          </Group>
        ) : null}
        {logs.isPending ? (
          <LoadingState />
        ) : logs.isError ? (
          <ErrorState error={logs.error} onRetry={() => void logs.refetch()} />
        ) : (
          <>
            <Card padding={0} className={classes.card} aria-busy={logs.isPlaceholderData || undefined}>
              {logs.data.items.length ? (
                <Table.ScrollContainer minWidth={760} type="native">
                  <Table verticalSpacing="sm" aria-label="Log entries">
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th scope="col">When</Table.Th>
                        <Table.Th scope="col">What</Table.Th>
                        <Table.Th scope="col">By</Table.Th>
                        <Table.Th scope="col">About</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {logs.data.items.map((entry) => (
                        <Row key={entry.id} entry={entry} linked={logs.data.accounts_linked} />
                      ))}
                    </Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
              ) : (
                <div className={classes.empty}>
                  <EmptyState>
                    {filtered
                      ? 'Nothing in the log matches these filters.'
                      : 'Nothing has been recorded yet.'}
                  </EmptyState>
                </div>
              )}
            </Card>
            <Group justify="space-between" gap="sm">
              <Text size="sm" c="dimmed" role="status">
                {logs.data.total === 1 ? '1 entry' : `${String(logs.data.total)} entries`}
              </Text>
              {logs.data.pages > 1 ? (
                <Pagination
                  total={logs.data.pages}
                  value={logs.data.page}
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
