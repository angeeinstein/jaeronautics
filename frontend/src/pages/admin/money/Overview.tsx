/**
 * The money on Stripe for a period, read from the portal's copy of Stripe's
 * book (services/money_overview.py) -- at once, without asking Stripe: by
 * what it was for, what Stripe holds and whose it is, and where Stripe and the
 * portal's records differ (services/money_checks.py), with the switch for
 * emailing those differences.
 */
import {
  Button,
  Checkbox,
  Collapse,
  List,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  VisuallyHidden,
} from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call } from '../../../api/client';
import { ErrorState, LoadingState } from '../../../components/States';
import { formatDate, formatDateTime, formatEuros } from '../../../lib/format';
import { notifyFailed } from '../../../lib/notify';
import classes from './Money.module.css';
import { type OverviewOut, overviewQuery } from './shared';

type Totals = OverviewOut['books']['total'];
type Difference = OverviewOut['differences'][number];

const PRODUCTS = [
  ['membership', 'Membership'],
  ['team', 'Teams'],
  ['credit', 'Credit'],
  ['other', 'Other'],
  ['total', 'Total'],
] as const;

/** One kind of booking across the products; what takes money back is shown as minus. */
function Row({
  books,
  label,
  field,
  sign = 1,
  strong = false,
}: {
  books: OverviewOut['books'];
  label: string;
  field: keyof Totals;
  sign?: 1 | -1;
  strong?: boolean;
}) {
  return (
    <Table.Tr className={strong ? classes.strong : undefined}>
      <Table.Th scope="row">{label}</Table.Th>
      {PRODUCTS.map(([key]) => (
        <Table.Td key={key} className={`${classes.amount} ja-figures`}>
          {formatEuros(sign * books[key][field] || 0)}
        </Table.Td>
      ))}
    </Table.Tr>
  );
}

function Books({ overview }: { overview: OverviewOut }) {
  const { books } = overview;
  const title = overview.since
    ? `${formatDate(overview.since)} to ${formatDate(overview.until)}`
    : `From the start to ${formatDate(overview.until)}`;
  return (
    <Stack gap="xs">
      <Title order={3} size="h5">
        {title}
      </Title>
      <Table.ScrollContainer minWidth={700} type="native">
        <Table verticalSpacing={6} aria-label="By what it was for">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">
                <VisuallyHidden>Booking</VisuallyHidden>
              </Table.Th>
              {PRODUCTS.map(([key, label]) => (
                <Table.Th key={key} scope="col" className={classes.amount}>
                  {label}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            <Row books={books} label="Received" field="received" />
            {books.total.returned ? (
              <Row books={books} label="SEPA debits that bounced" field="returned" sign={-1} />
            ) : null}
            <Row books={books} label="Refunded" field="refunded" sign={-1} />
            <Row books={books} label="Taken back (chargebacks)" field="taken_back" sign={-1} />
            <Row books={books} label="Stripe's fees" field="fees" sign={-1} />
            {books.other_bookings ? (
              <Table.Tr>
                <Table.Th scope="row" colSpan={PRODUCTS.length}>
                  Other bookings
                </Table.Th>
                <Table.Td className={`${classes.amount} ja-figures`}>
                  {formatEuros(books.other_bookings)}
                </Table.Td>
              </Table.Tr>
            ) : null}
            <Row books={books} label="Net" field="net" strong />
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {books.stripe_fees ? (
        <Text size="sm" c="dimmed">
          {`Stripe's fees include ${formatEuros(books.stripe_fees)} Stripe billed on its own.`}
        </Text>
      ) : null}
    </Stack>
  );
}

function Holdings({ overview }: { overview: OverviewOut }) {
  const { books } = overview;
  const rows: [string, number, boolean][] = [
    ['In Stripe', books.balance_at_end, true],
    ["of which the teams' (still to be transferred)", -overview.owed_to_teams, false],
    ["of which members' credit (topped up through Stripe)", -overview.credit_held.top_ups, false],
    ["The association's own", overview.association_own, true],
  ];
  return (
    <Stack gap="xs">
      <Title order={3} size="h5">
        On {formatDate(overview.until)}
      </Title>
      <Table verticalSpacing={6} aria-label={`On ${formatDate(overview.until)}`}>
        <Table.Tbody>
          {rows.map(([label, cents, strong]) => (
            <Table.Tr key={label} className={strong ? classes.strong : undefined}>
              <Table.Th scope="row" className={strong ? undefined : classes.indent}>
                {label}
              </Table.Th>
              <Table.Td className={`${classes.amount} ja-figures`}>{formatEuros(cents)}</Table.Td>
            </Table.Tr>
          ))}
          <Table.Tr>
            <Table.Th scope="row">Paid out to the bank account in the period</Table.Th>
            <Table.Td className={`${classes.amount} ja-figures`}>{formatEuros(books.paid_out)}</Table.Td>
          </Table.Tr>
        </Table.Tbody>
      </Table>
      {overview.credit_held.cash ? (
        <Text size="sm" c="dimmed">
          {`Members also hold ${formatEuros(overview.credit_held.cash)} of credit handed over in cash or booked by hand: not in Stripe.`}
        </Text>
      ) : null}
    </Stack>
  );
}

function differenceLine(difference: Difference): string {
  const amounts = [
    difference.amount === null ? null : `portal ${formatEuros(difference.amount)}`,
    difference.stripe_amount === null ? null : `Stripe ${formatEuros(difference.stripe_amount)}`,
  ].filter(Boolean);
  return [difference.what, difference.reference ?? '–', amounts.join(', '), difference.who]
    .filter(Boolean)
    .join(' · ');
}

function EmailSwitch({ on }: { on: boolean }) {
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: async (next: boolean) => {
      const current = await call(api.GET('/api/v1/admin/money/settings'));
      return call(api.PUT('/api/v1/admin/money/settings', { body: { ...current, difference_emails: next } }));
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ['admin', 'money'] }),
    onError: notifyFailed,
  });
  return (
    <Checkbox
      label="Email differences to the admins and the treasurer"
      description="Once each, when one has lasted an hour."
      checked={save.isPending ? save.variables : on}
      disabled={save.isPending}
      onChange={(event) => {
        save.mutate(event.currentTarget.checked);
      }}
    />
  );
}

function Differences({ overview }: { overview: OverviewOut }) {
  const [resolved, setResolved] = useState(false);
  const { differences } = overview;
  return (
    <Stack gap="xs">
      <Title order={3} size="h5">
        Against the portal
      </Title>
      {differences.length ? (
        <>
          <Text c="var(--ja-warning)">
            {`${String(differences.length)} ${differences.length === 1 ? 'difference' : 'differences'}:`}
          </Text>
          <List size="sm" spacing={4}>
            {differences.map((difference) => (
              <List.Item key={difference.id}>
                {differenceLine(difference)}
                <Text size="xs" c="dimmed">
                  {`Since ${formatDateTime(difference.first_seen_at)}${difference.emailed_at ? ', emailed' : ''}`}
                </Text>
              </List.Item>
            ))}
          </List>
        </>
      ) : (
        <Text c="var(--ja-success-text)">
          Every payment in Stripe matches the portal&apos;s records, and the other way round.
        </Text>
      )}
      {overview.resolved.length ? (
        <>
          <Collapse expanded={resolved}>
            <List size="sm" spacing={4} c="dimmed">
              {overview.resolved.map((difference) => (
                <List.Item key={difference.id}>
                  {`${differenceLine(difference)} — resolved ${difference.resolved_at ? formatDateTime(difference.resolved_at) : ''}`}
                </List.Item>
              ))}
            </List>
          </Collapse>
          <Button
            variant="subtle"
            size="compact-sm"
            w="fit-content"
            aria-expanded={resolved}
            onClick={() => {
              setResolved(!resolved);
            }}
          >
            {resolved
              ? 'Hide resolved'
              : `Resolved in the last 30 days (${String(overview.resolved.length)})`}
          </Button>
        </>
      ) : null}
      {overview.checked_at ? (
        <Text size="sm" c="dimmed">
          Checked {formatDateTime(overview.checked_at)}, after each fetch.
        </Text>
      ) : null}
      <EmailSwitch on={overview.emails} />
    </Stack>
  );
}

export function Overview({ since, until }: { since: string | null; until: string | null }) {
  const overview = useQuery(overviewQuery(since, until));
  if (overview.isPending) return <LoadingState />;
  if (overview.isError) return <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />;
  if (!overview.data.fetched_at) {
    // Nothing fetched yet: no figures rather than zeros.
    return (
      <LoadingState label={overview.data.fetching ? "Fetching Stripe's book…" : 'Nothing fetched yet.'} />
    );
  }
  return (
    <Stack gap="lg">
      <Books overview={overview.data} />
      <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="xl">
        <Holdings overview={overview.data} />
        <Differences overview={overview.data} />
      </SimpleGrid>
    </Stack>
  );
}

/** When the book was fetched, whether it adds up to what Stripe holds, and a way to fetch now. */
export function FetchState({ overview }: { overview: OverviewOut }) {
  const balanced =
    overview.stripe_balance !== null &&
    overview.books_balance !== null &&
    overview.stripe_balance === overview.books_balance;
  if (!overview.fetched_at) {
    return (
      <Text size="sm" c="dimmed">
        {overview.fetching
          ? 'Fetching Stripe’s book for the first time. It reads all of it and takes a few minutes; the figures appear when it is done.'
          : 'Stripe’s book has not been fetched yet. The first fetch reads all of it and takes a few minutes.'}
      </Text>
    );
  }
  return (
    <Stack gap={2}>
      <Text size="sm" c="dimmed">
        {`Fetched from Stripe ${formatDateTime(overview.fetched_at)}: after every payment, and at least hourly.`}
        {overview.fetching ? ' Fetching again now…' : ''}
      </Text>
      {overview.last_error ? (
        <Text size="sm" c="var(--ja-warning)">
          {`The last fetch failed: ${overview.last_error}`}
        </Text>
      ) : balanced ? (
        <Text size="sm" c="var(--ja-success-text)">
          Its bookings add up to what Stripe holds.
        </Text>
      ) : (
        <Text size="sm" c="var(--ja-warning)">
          {`Its bookings add up to ${formatEuros(overview.books_balance ?? 0)}, but Stripe held ${formatEuros(overview.stripe_balance ?? 0)}. A booking in another currency, or one made that very moment, can explain it.`}
        </Text>
      )}
    </Stack>
  );
}
