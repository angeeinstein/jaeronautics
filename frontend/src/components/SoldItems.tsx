/**
 * What a seller sold for credit, by item (docs/credit-plan.md, "Where it is
 * shown"): how many of each and what each made, the last 30 days and ever
 * -- "to see what beverages are liked by the members and how much money they
 * made with each item". A sale taken back counts for neither. Data: a
 * SellerSalesOut (api/credit.py).
 */
import { Group, Stack, Table, Text } from '@mantine/core';

import type { Schemas } from '../api/client';
import { formatEuros } from '../lib/format';
import { ItemIcon } from './ItemIcon';
import { EmptyState } from './States';

export type SellerSales = Schemas['SellerSalesOut'];

const figure = { textAlign: 'right', whiteSpace: 'nowrap' } as const;

export function SoldItems({ seller, label }: { seller: SellerSales; label: string }) {
  if (!seller.items.length)
    return (
      <Stack px="md" pb="md">
        <EmptyState>Nothing sold yet.</EmptyState>
      </Stack>
    );
  return (
    <Table.ScrollContainer minWidth={560} type="native">
      <Table verticalSpacing="sm" aria-label={label}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Item</Table.Th>
            <Table.Th scope="col" style={figure}>
              Sold, 30 days
            </Table.Th>
            <Table.Th scope="col" style={figure}>
              Made, 30 days
            </Table.Th>
            <Table.Th scope="col" style={figure}>
              Sold, ever
            </Table.Th>
            <Table.Th scope="col" style={figure}>
              Made, ever
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {seller.items.map((item) => (
            <Table.Tr key={item.name}>
              <Table.Td>
                <Group gap="xs" wrap="nowrap">
                  <ItemIcon icon={item.icon} />
                  <Text size="sm">{item.name}</Text>
                </Group>
              </Table.Td>
              <Table.Td style={figure} className="ja-figures">
                {item.sold_30_days}
              </Table.Td>
              <Table.Td style={figure} className="ja-figures">
                {formatEuros(item.earned_30_days)}
              </Table.Td>
              <Table.Td style={figure} className="ja-figures">
                {item.sold}
              </Table.Td>
              <Table.Td style={figure} className="ja-figures">
                {formatEuros(item.earned)}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
        <Table.Tfoot>
          <Table.Tr>
            <Table.Th scope="row">Together</Table.Th>
            <Table.Th style={figure} className="ja-figures">
              {seller.sold_30_days}
            </Table.Th>
            <Table.Th style={figure} className="ja-figures">
              {formatEuros(seller.earned_30_days)}
            </Table.Th>
            <Table.Th style={figure} className="ja-figures">
              {seller.sold}
            </Table.Th>
            <Table.Th style={figure} className="ja-figures">
              {formatEuros(seller.earned)}
            </Table.Th>
          </Table.Tr>
        </Table.Tfoot>
      </Table>
    </Table.ScrollContainer>
  );
}
