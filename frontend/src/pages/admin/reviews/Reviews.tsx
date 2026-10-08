/**
 * Reviews (docs/frontend-structure.md, 4): what waits for a yes or no, oldest
 * first and one card per item, each decided on its own; the forum sync
 * problems; and the past decisions, folded until asked for. Each kind only
 * for whoever decides it -- the server leaves out the rest. Data:
 * GET /api/v1/admin/reviews (aeronautics_members/api/admin_reviews.py).
 *
 * The dashboard links here with #review-queue and #sync-problems.
 */
import { Group, Pagination, Stack, Table, Text, UnstyledButton } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useLocation } from 'react-router';

import { AppLink } from '../../../app/AppLink';
import { can, useMe } from '../../../api/session';
import { AllClear } from '../../../components/AllClear';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { ErrorState, LoadingState } from '../../../components/States';
import { formatDateTime } from '../../../lib/format';
import { useArrivals } from '../../../lib/arrivals';
import { EVERY_30_SECONDS, useLiveRefresh } from '../../../lib/live';
import { ReviewCard } from './ReviewCard';
import { historyQuery, type Reviews as ReviewsData, reviewsQuery } from './shared';
import classes from './Reviews.module.css';

const itemKey = (item: ReviewsData['queue'][number]) => `${item.kind}-${String(item.id)}`;

function Queue({ data, canOpenAccounts }: { data: ReviewsData; canOpenAccounts: boolean }) {
  const waiting = (data.waiting.name_changes ?? 0) + (data.waiting.pictures ?? 0);
  const arrived = useArrivals('queue', data.queue.map(itemKey));
  if (!data.queue.length) {
    return (
      <div id="review-queue">
        <AllClear
          title="Nothing waiting for review"
          detail="New change requests and profile pictures appear here, and on the dashboard."
        />
      </div>
    );
  }
  return (
    <Stack gap="md" id="review-queue">
      <Group justify="space-between">
        <Text fw={600}>Waiting for review</Text>
        <Text size="sm" c="dimmed" className="ja-figures">
          {waiting}
        </Text>
      </Group>
      {data.queue.map((item) => (
        <ReviewCard
          key={itemKey(item)}
          item={item}
          canOpenAccount={canOpenAccounts}
          arrived={arrived.has(itemKey(item))}
        />
      ))}
      {waiting > data.queue.length ? (
        <Text size="sm" c="dimmed">
          The oldest {data.queue.length} of {waiting}. The rest appear here as these are decided.
        </Text>
      ) : null}
    </Stack>
  );
}

function SyncProblems({ data, canOpenAccounts }: { data: ReviewsData; canOpenAccounts: boolean }) {
  if (!data.sync_problems?.length) return null;
  return (
    <div id="sync-problems">
      <Panel
        title="Forum sync problems"
        flush
        actions={<Pill tone="failed">{String(data.waiting.sync_problems ?? data.sync_problems.length)}</Pill>}
      >
        <Table.ScrollContainer minWidth={640} type="native">
          <Table verticalSpacing="sm" aria-label="Forum sync problems">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Account</Table.Th>
                <Table.Th scope="col">Problem</Table.Th>
                <Table.Th scope="col">Last synced</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.sync_problems.map((problem, index) => (
                <Table.Tr key={problem.user_id ?? `unknown-${String(index)}`}>
                  <Table.Td>
                    {canOpenAccounts && problem.user_id ? (
                      // Synced again from the account's Forum tab.
                      <AppLink to={`/admin/accounts/${String(problem.user_id)}#forum`}>
                        {problem.email}
                      </AppLink>
                    ) : (
                      (problem.email ?? '–')
                    )}
                  </Table.Td>
                  <Table.Td>{problem.error ?? 'The account is marked as out of sync.'}</Table.Td>
                  <Table.Td className="ja-figures">
                    {problem.last_synced_at ? formatDateTime(problem.last_synced_at) : 'never'}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Panel>
    </div>
  );
}

const DECISION = {
  approved: { label: 'Approved', tone: 'active' },
  rejected: { label: 'Rejected', tone: 'failed' },
  withdrawn: { label: 'Withdrawn', tone: 'neutral' },
} as const;

function HistoryTable({ page, onPage }: { page: number; onPage: (page: number) => void }) {
  const history = useQuery({ ...historyQuery(page), placeholderData: keepPreviousData });
  if (history.isPending) return <LoadingState />;
  if (history.isError) return <ErrorState error={history.error} onRetry={() => void history.refetch()} />;
  return (
    <>
      <Table.ScrollContainer minWidth={640} type="native">
        <Table verticalSpacing="sm" aria-label="Past decisions">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">When</Table.Th>
              <Table.Th scope="col">What</Table.Th>
              <Table.Th scope="col">Member</Table.Th>
              <Table.Th scope="col">Decision</Table.Th>
              <Table.Th scope="col">By</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {history.data.items.map((item) => (
              <Table.Tr key={`${item.kind}-${String(item.id)}`}>
                <Table.Td className="ja-figures">{formatDateTime(item.at)}</Table.Td>
                <Table.Td>
                  {item.kind === 'picture'
                    ? 'Profile picture'
                    : item.is_name_change
                      ? 'Name change'
                      : 'Details change'}
                  {item.requested_full_name ? (
                    <Text size="sm" c="dimmed">
                      {item.requested_full_name}
                    </Text>
                  ) : null}
                </Table.Td>
                <Table.Td>{item.member_email ?? '–'}</Table.Td>
                <Table.Td>
                  <Pill tone={DECISION[item.decision].tone}>{DECISION[item.decision].label}</Pill>
                </Table.Td>
                <Table.Td>{item.by ?? '–'}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {history.data.pages > 1 ? (
        <Group justify="flex-end" p="md">
          <Pagination
            total={history.data.pages}
            value={history.data.page}
            onChange={onPage}
            size="sm"
            getItemProps={(item) => ({ 'aria-label': `Page ${String(item)}` })}
          />
        </Group>
      ) : null}
    </>
  );
}

function History() {
  const [open, { toggle }] = useDisclosure(false);
  const [page, setPage] = useState(1);
  return (
    <div id="review-history">
      <Panel
        title="History"
        flush
        actions={
          <UnstyledButton className={classes.fold} onClick={toggle} aria-expanded={open}>
            {open ? 'Hide past decisions' : 'Show past decisions'}
          </UnstyledButton>
        }
      >
        {open ? <HistoryTable page={page} onPage={setPage} /> : null}
      </Panel>
    </div>
  );
}

export function Reviews() {
  const reviews = useQuery(reviewsQuery);
  useLiveRefresh(reviewsQuery.queryKey, EVERY_30_SECONDS);
  const me = useMe();
  const { hash } = useLocation();
  const canOpenAccounts = can(me.data, 'accounts.view');

  // A link to a part of the page (from the dashboard) lands on it once it is there.
  useEffect(() => {
    if (reviews.data && hash) document.getElementById(hash.slice(1))?.scrollIntoView();
  }, [reviews.data, hash]);

  return (
    <>
      <PageHeader
        title="Reviews"
        description="Change requests and profile pictures waiting for a decision, and forum sync problems."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Reviews' }]}
      />
      {reviews.isPending ? (
        <LoadingState />
      ) : reviews.isError ? (
        <ErrorState error={reviews.error} onRetry={() => void reviews.refetch()} />
      ) : (
        <Stack gap="xl">
          <Queue data={reviews.data} canOpenAccounts={canOpenAccounts} />
          <SyncProblems data={reviews.data} canOpenAccounts={canOpenAccounts} />
          <History />
        </Stack>
      )}
    </>
  );
}
