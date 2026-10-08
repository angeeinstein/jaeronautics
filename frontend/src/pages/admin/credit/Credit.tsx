/**
 * Admin › Credit (docs/credit-plan.md): what the association holds for its
 * members, everybody with credit -- each a way to their page, where cash is
 * booked -- and the latest entries. For admins and the association's
 * treasurer (credit.manage). Data: GET /api/v1/admin/credit
 * (aeronautics_members/api/credit.py).
 */
import { Alert, Anchor, Avatar, Group, SimpleGrid, Stack, Table, Text } from '@mantine/core';
import { IconDownload } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';

import { AppLink } from '../../../app/AppLink';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { StatTile } from '../../../components/StatTile';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { formatDateTime, formatEuros, plural } from '../../../lib/format';
import { EVERY_MINUTE, useLiveRefresh } from '../../../lib/live';
import { signedEuros } from '../../account/creditData';
import classes from './Credit.module.css';
import { type CreditOverview, creditOverviewQuery, initialsOf } from './shared';

function People({ people }: { people: CreditOverview['people'] }) {
  if (!people.length) return <EmptyState>Nobody has credit yet.</EmptyState>;
  return (
    <Table.ScrollContainer minWidth={420} type="native">
      <Table verticalSpacing="sm" highlightOnHover aria-label="Balances">
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Person</Table.Th>
            <Table.Th scope="col" className={classes.amount}>
              Credit
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {people.map((person) => (
            <Table.Tr key={person.user_id}>
              <Table.Td>
                <Group gap="sm" wrap="nowrap">
                  <Avatar src={person.picture_url} alt="" size={32} color="brand" variant="filled">
                    {initialsOf(person.name)}
                  </Avatar>
                  <AppLink to={`/admin/credit/${String(person.user_id)}`} className={classes.name}>
                    {person.name}
                  </AppLink>
                  {person.erased ? <Pill tone="neutral">Erased</Pill> : null}
                </Group>
              </Table.Td>
              <Table.Td
                className={`${classes.amount} ja-figures`}
                data-negative={person.balance_cents < 0 || undefined}
              >
                {formatEuros(person.balance_cents)}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function Recent({ entries }: { entries: CreditOverview['recent'] }) {
  if (!entries.length) return <EmptyState>Nothing booked yet.</EmptyState>;
  return (
    <Table.ScrollContainer minWidth={640} type="native">
      <Table verticalSpacing="sm" aria-label="Latest entries">
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">When</Table.Th>
            <Table.Th scope="col">Person</Table.Th>
            <Table.Th scope="col">What</Table.Th>
            <Table.Th scope="col" className={classes.amount}>
              Amount
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {entries.map((entry) => (
            <Table.Tr key={entry.id}>
              <Table.Td className={classes.when}>{formatDateTime(entry.at)}</Table.Td>
              <Table.Td>
                <AppLink to={`/admin/credit/${String(entry.user_id)}`} className={classes.name}>
                  {entry.name}
                </AppLink>
              </Table.Td>
              <Table.Td>
                <span className={classes.what}>
                  <span>{entry.kind_label}</span>
                  {entry.description !== entry.kind_label ? (
                    <span className={classes.muted}>{entry.description}</span>
                  ) : null}
                </span>
              </Table.Td>
              <Table.Td className={`${classes.amount} ja-figures`} data-sign={Math.sign(entry.amount_cents)}>
                {signedEuros(entry.amount_cents)}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

export function Credit() {
  const overview = useQuery(creditOverviewQuery);
  useLiveRefresh(creditOverviewQuery.queryKey, EVERY_MINUTE);
  return (
    <>
      <PageHeader
        title="Credit"
        description="What members hold, and every change of it."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Credit' }]}
        actions={
          <Anchor href="/admin/credit/export.csv" size="sm" className={classes.download}>
            <IconDownload size={16} aria-hidden /> Download all
          </Anchor>
        }
      />
      {overview.isPending ? (
        <LoadingState />
      ) : overview.isError ? (
        <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
      ) : (
        <Stack gap="lg">
          {!overview.data.enabled ? (
            <Alert color="gray" variant="light">
              Credit is switched off (Settings › Credit). Nobody can top up; balances are kept.
            </Alert>
          ) : null}
          {overview.data.mismatched.length ? (
            <Alert color="red" variant="light" title="Balances that do not add up">
              {`${String(overview.data.mismatched.length)} balance(s) are not the sum of their entries. Nothing was changed; tell whoever runs the portal.`}
            </Alert>
          ) : null}
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
            <StatTile
              value={formatEuros(overview.data.totals.held_cents)}
              label="Held for members"
              note={`by ${String(overview.data.totals.people)} ${plural(overview.data.totals.people, 'person', 'people')}`}
            />
            <StatTile
              value={formatEuros(overview.data.totals.came_in_cents)}
              label="Came in"
              note="Top-ups and cash, last 30 days"
            />
            <StatTile
              value={formatEuros(overview.data.totals.spent_cents)}
              label="Spent"
              note="Last 30 days"
            />
          </SimpleGrid>
          <Panel title="Balances" flush>
            <div className={classes.inset}>
              <People people={overview.data.people} />
            </div>
          </Panel>
          <Panel title="Latest" flush>
            <div className={classes.inset}>
              <Recent entries={overview.data.recent} />
            </div>
          </Panel>
          <Text size="sm" c="dimmed">
            Cash handed over, credit paid out, corrections and refunds are booked on a person&apos;s page.
          </Text>
        </Stack>
      )}
    </>
  );
}
