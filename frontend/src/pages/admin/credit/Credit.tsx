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

import { api, call } from '../../../api/client';

import { AppLink } from '../../../app/AppLink';
import { PageHeader } from '../../../components/PageHeader';
import { type ItemChange, PriceListPanel } from '../../../components/PriceList';
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

function Sellers({ sellers }: { sellers: CreditOverview['sellers'] }) {
  if (!sellers.some((seller) => seller.earned || seller.items)) return null;
  return (
    <Panel title="Sold" flush>
      <div className={classes.inset}>
        <Table.ScrollContainer minWidth={480} type="native">
          <Table verticalSpacing="sm" aria-label="Sold">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Seller</Table.Th>
                <Table.Th scope="col" className={classes.amount}>
                  Items
                </Table.Th>
                <Table.Th scope="col" className={classes.amount}>
                  Last 30 days
                </Table.Th>
                <Table.Th scope="col" className={classes.amount}>
                  Ever
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sellers.map((seller) => (
                <Table.Tr key={seller.team_slug ?? 'association'}>
                  <Table.Td>
                    {seller.team_slug ? (
                      <AppLink to={`/admin/money/${seller.team_slug}`} className={classes.name}>
                        {seller.name}
                      </AppLink>
                    ) : (
                      <span className={classes.name}>{seller.name}</span>
                    )}
                  </Table.Td>
                  <Table.Td className={`${classes.amount} ja-figures`}>{seller.items}</Table.Td>
                  <Table.Td className={`${classes.amount} ja-figures`}>
                    {formatEuros(seller.earned_30_days)}
                  </Table.Td>
                  <Table.Td className={`${classes.amount} ja-figures`}>{formatEuros(seller.earned)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </div>
    </Panel>
  );
}

const associationPricesKey = ['admin', 'credit', 'items'] as const;

/** The association's own price list: coffee in the student area, say. */
function AssociationPrices() {
  const list = useQuery({
    queryKey: associationPricesKey,
    queryFn: () => call(api.GET('/api/v1/admin/credit/items')),
  });
  if (!list.data) return null;
  return (
    <PriceListPanel
      title="The association's price list"
      list={list.data}
      queryKey={associationPricesKey}
      add={(body) => call(api.POST('/api/v1/admin/credit/items', { body }))}
      change={(id: number, body: ItemChange) =>
        call(api.PUT('/api/v1/admin/credit/items/{item_id}', { params: { path: { item_id: id } }, body }))
      }
    />
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
          <Sellers sellers={overview.data.sellers} />
          <AssociationPrices />
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
            Cash handed over, credit paid out, corrections, sales and refunds are booked on a person&apos;s
            page. A team keeps its own price list, on its Prices page.
          </Text>
        </Stack>
      )}
    </>
  );
}
