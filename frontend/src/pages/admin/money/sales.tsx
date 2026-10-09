/**
 * What both sides of a team's money show alike (the team's page and Admin ›
 * Money › a team): what the team sold for credit, by item; what of a
 * payment counts for the team, with Stripe's fee where the payment carries
 * it; and the sentence saying who carries the fees.
 */
import { Stack, Table, Text } from '@mantine/core';

import type { Schemas } from '../../../api/client';
import { Panel } from '../../../components/Panel';
import { EmptyState } from '../../../components/States';
import { formatEuros, plural } from '../../../lib/format';

type Payment = Schemas['TeamPayment'];
type Sales = Schemas['CreditSales'];

/** What of a payment counts for the team: less refunds, and less Stripe's fee where it carries it. */
export function Counts({ payment }: { payment: Payment }) {
  return (
    <>
      <span className="ja-figures">{payment.counts === null ? '–' : formatEuros(payment.counts)}</span>
      {payment.disputed ? (
        <Text size="xs" c="var(--ja-warning)">
          Taken back (chargeback)
        </Text>
      ) : null}
      {!payment.disputed && payment.refunded ? (
        <Text size="xs" c="dimmed">{`${formatEuros(payment.refunded)} refunded`}</Text>
      ) : null}
      {payment.counts === null ? (
        <Text size="xs" c="dimmed">
          Waiting for Stripe&apos;s fee
        </Text>
      ) : payment.fee !== null && payment.fee !== undefined ? (
        <Text size="xs" c="dimmed">{`${formatEuros(payment.fee)} Stripe fee`}</Text>
      ) : null}
    </>
  );
}

/** Who carries Stripe's fees, as these payments were made; and what waits for its fee. */
export function feeNote(payments: Payment[], waitingForFee: number): string {
  const carried = payments.some((payment) => payment.counts === null || payment.fee != null);
  const who = carried
    ? "Payments made since the team carries Stripe's fee count less that fee."
    : "Stripe's fees are paid by the association.";
  const waiting = waitingForFee
    ? ` ${formatEuros(waitingForFee)} counts once Stripe has said its fee (overnight).`
    : '';
  return `The team gets what its members paid, less refunds, and what it sold for credit. ${who}${waiting}`;
}

/** What the team sold for credit, by item: sales that stand, less the association's share where it keeps one. */
export function SalesPanel({ sales }: { sales: Sales }) {
  return (
    <Panel
      title="Sold for credit"
      flush
      actions={
        <Text size="sm" c="dimmed">
          {`${String(sales.count)} ${plural(sales.count, 'sale', 'sales')}`}
        </Text>
      }
    >
      {sales.by_item.length ? (
        <Table.ScrollContainer minWidth={360} type="native">
          <Table verticalSpacing="sm" aria-label="Sold for credit">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Item</Table.Th>
                <Table.Th scope="col" ta="right">
                  Sold
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  Counts
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sales.by_item.map((row) => (
                <Table.Tr key={row.name}>
                  <Table.Td>{row.name}</Table.Td>
                  <Table.Td ta="right" className="ja-figures">
                    {row.count}
                  </Table.Td>
                  <Table.Td ta="right" className="ja-figures">
                    {formatEuros(row.earned)}
                  </Table.Td>
                </Table.Tr>
              ))}
              <Table.Tr>
                <Table.Th scope="row">Together</Table.Th>
                <Table.Td ta="right" className="ja-figures">
                  {sales.count}
                </Table.Td>
                <Table.Td ta="right" className="ja-figures" fw={600}>
                  {formatEuros(sales.earned)}
                </Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      ) : (
        <Stack px="md">
          <EmptyState>Nothing sold yet.</EmptyState>
        </Stack>
      )}
    </Panel>
  );
}
