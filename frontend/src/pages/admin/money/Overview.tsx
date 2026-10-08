/**
 * The money on Stripe for a period (services/money_overview.py): by what it
 * was for, what of it is the teams', what Stripe holds, and the check of
 * every paid invoice against the portal's records.
 */
import { Button, Collapse, List, SimpleGrid, Stack, Table, Text, Title, VisuallyHidden } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { ErrorState, LoadingState } from '../../../components/States';
import { formatDate, formatDateTime, formatEuros } from '../../../lib/format';
import classes from './Money.module.css';
import { type OverviewOut, overviewQuery } from './shared';

type Totals = OverviewOut['books']['total'];
type Mismatch = OverviewOut['mismatches'][number];

const PRODUCTS = [
  ['membership', 'Membership'],
  ['team', 'Teams'],
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
          {formatEuros(sign * books[key][field])}
        </Table.Td>
      ))}
    </Table.Tr>
  );
}

function Books({ overview }: { overview: OverviewOut }) {
  const { books } = overview;
  const title = overview.since
    ? `${formatDate(overview.since)} to ${formatDate(overview.until)} on Stripe`
    : `From the start to ${formatDate(overview.until)} on Stripe`;
  return (
    <Stack gap="xs">
      <Title order={3} size="h5">
        {title}
      </Title>
      <Table.ScrollContainer minWidth={620} type="native">
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
                <Table.Th scope="row" colSpan={4}>
                  Other bookings
                </Table.Th>
                <Table.Td className={`${classes.amount} ja-figures`}>
                  {formatEuros(books.other_bookings)}
                </Table.Td>
              </Table.Tr>
            ) : null}
            <Row books={books} label="Net" field="net" strong />
            <Table.Tr>
              <Table.Th scope="row" colSpan={4} className={classes.indent}>
                of which the teams&apos; (what their members paid)
              </Table.Th>
              <Table.Td className={`${classes.amount} ja-figures`}>
                {formatEuros(overview.teams_share)}
              </Table.Td>
            </Table.Tr>
            <Table.Tr className={classes.strong}>
              <Table.Th scope="row" colSpan={4} className={classes.indent}>
                of which the association&apos;s own
              </Table.Th>
              <Table.Td className={`${classes.amount} ja-figures`}>
                {formatEuros(overview.association_own)}
              </Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Text size="sm" c="dimmed">
        Other is everything paid without a membership or team invoice: the webshop, events, payment links.
        {books.stripe_fees
          ? ` Stripe's fees include ${formatEuros(books.stripe_fees)} Stripe billed on its own.`
          : ''}
      </Text>
    </Stack>
  );
}

function Holdings({ overview }: { overview: OverviewOut }) {
  const { books } = overview;
  const now = overview.in_stripe_now;
  const nowTotal = now ? now.available + now.pending : null;
  return (
    <Stack gap="xs">
      <Title order={3} size="h5">
        On {formatDate(overview.until)}
      </Title>
      <Table verticalSpacing={6} aria-label={`On ${formatDate(overview.until)}`}>
        <Table.Tbody>
          <Table.Tr className={classes.strong}>
            <Table.Th scope="row">In Stripe</Table.Th>
            <Table.Td className={`${classes.amount} ja-figures`}>
              {formatEuros(books.balance_at_end)}
            </Table.Td>
          </Table.Tr>
          {now ? (
            <Table.Tr>
              <Table.Th scope="row" className={classes.indent}>
                available now / on its way
              </Table.Th>
              <Table.Td className={`${classes.amount} ja-figures`}>
                {formatEuros(now.available)} / {formatEuros(now.pending)}
              </Table.Td>
            </Table.Tr>
          ) : null}
          <Table.Tr>
            <Table.Th scope="row">Paid out to the bank account in the period</Table.Th>
            <Table.Td className={`${classes.amount} ja-figures`}>{formatEuros(books.paid_out)}</Table.Td>
          </Table.Tr>
          <Table.Tr>
            <Table.Th scope="row">Still owed to the teams</Table.Th>
            <Table.Td className={`${classes.amount} ja-figures`}>
              {formatEuros(overview.owed_to_teams)}
            </Table.Td>
          </Table.Tr>
        </Table.Tbody>
      </Table>
      {nowTotal === null ? null : nowTotal === books.balance_at_end ? (
        <Text size="sm" c="var(--ja-success-text)">
          Stripe&apos;s bookings add up to the balance Stripe holds now.
        </Text>
      ) : (
        <Text size="sm" c="var(--ja-warning)">
          Stripe&apos;s bookings add up to {formatEuros(books.balance_at_end)}, but Stripe holds{' '}
          {formatEuros(nowTotal)} now. A booking in another currency, or one made this very moment, can
          explain it.
        </Text>
      )}
    </Stack>
  );
}

function mismatchLine(mismatch: Mismatch): string {
  const amounts =
    mismatch.amount === null
      ? ''
      : `, ${formatEuros(mismatch.amount)}${mismatch.stripe_amount === null ? '' : ` / ${formatEuros(mismatch.stripe_amount)}`}`;
  return `${mismatch.what}: ${mismatch.invoice ?? '–'}${amounts}${mismatch.who ? `, ${mismatch.who}` : ''}`;
}

const SHOWN = 10;

function Check({ overview }: { overview: OverviewOut }) {
  const [all, setAll] = useState(false);
  const { mismatches } = overview;
  return (
    <Stack gap="xs">
      <Title order={3} size="h5">
        Check against the portal
      </Title>
      {mismatches.length ? (
        <>
          <Text c="var(--ja-warning)">{mismatches.length} payment(s) do not match:</Text>
          <List size="sm" spacing={4}>
            {mismatches.slice(0, SHOWN).map((mismatch, index) => (
              <List.Item key={`${mismatch.invoice ?? ''}-${String(index)}`}>
                {mismatchLine(mismatch)}
              </List.Item>
            ))}
          </List>
          {mismatches.length > SHOWN ? (
            <>
              <Collapse expanded={all}>
                <List size="sm" spacing={4}>
                  {mismatches.slice(SHOWN).map((mismatch, index) => (
                    <List.Item key={`${mismatch.invoice ?? ''}-more-${String(index)}`}>
                      {mismatchLine(mismatch)}
                    </List.Item>
                  ))}
                </List>
              </Collapse>
              <Button
                variant="subtle"
                size="compact-sm"
                onClick={() => {
                  setAll(!all);
                }}
              >
                {all ? 'Show fewer' : `${String(mismatches.length - SHOWN)} more`}
              </Button>
            </>
          ) : null}
        </>
      ) : (
        <Text c="var(--ja-success-text)">
          Every paid membership and team invoice of the period matches the portal&apos;s records.
        </Text>
      )}
      <Text size="sm" c="dimmed">
        Checked {formatDateTime(overview.checked_at)}.
      </Text>
    </Stack>
  );
}

export function Overview({ since, until }: { since: string | null; until: string | null }) {
  const overview = useQuery(overviewQuery(since, until));
  if (overview.isPending) return <LoadingState label="Asking Stripe…" />;
  if (overview.isError) return <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />;
  return (
    <Stack gap="lg">
      <Books overview={overview.data} />
      <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="xl">
        <Holdings overview={overview.data} />
        <Check overview={overview.data} />
      </SimpleGrid>
    </Stack>
  );
}
