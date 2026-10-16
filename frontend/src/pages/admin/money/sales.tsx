/**
 * What both sides of a team's money show alike (the team's page and Admin ›
 * Money › a team): what the team sold for credit, by item; a payment's
 * Stripe fee, and what of the payment goes to the team.
 * How it all adds up: components/MoneyStatement.tsx.
 */
import { Stack, Table, Text } from '@mantine/core';
import { IconChartBar } from '@tabler/icons-react';

import type { Schemas } from '../../../api/client';
import { Panel } from '../../../components/Panel';
import { EmptyState } from '../../../components/States';
import { formatEuros, plural } from '../../../lib/format';

type Payment = Schemas['TeamPayment'];
type Sales = Schemas['CreditSales'];

/** Stripe's fee for a payment: taken off where the teams carry it, else carried by the association. */
export function Fee({ payment }: { payment: Payment }) {
  if (payment.fee === null || payment.fee === undefined) {
    return (
      <Text span size="sm" c="dimmed">
        not known yet
      </Text>
    );
  }
  if (payment.fee_carried_by === 'team') {
    return <span className="ja-figures">{formatEuros(-payment.fee)}</span>;
  }
  return (
    <>
      <Text span size="sm" c="dimmed" className="ja-figures">
        {formatEuros(payment.fee)}
      </Text>
      <Text size="xs" c="dimmed">
        carried by the association
      </Text>
    </>
  );
}

/** What of a payment goes to the team: less refunds, and less Stripe's fee where the teams carry it. */
export function ForTheTeam({ payment }: { payment: Payment }) {
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
      ) : null}
    </>
  );
}

/** What the team sold for credit, by item: sales that stand, less the association's share where it keeps one. */
export function SalesPanel({ sales }: { sales: Sales }) {
  return (
    <Panel
      icon={IconChartBar}
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
                  For the team
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
