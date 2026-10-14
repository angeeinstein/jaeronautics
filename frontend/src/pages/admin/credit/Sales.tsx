/**
 * Admin › Sales (docs/todo.md, "Credit, sales and devices"): what is sold for
 * credit -- by seller (the association and each team), and by item, how many
 * and what each made -- the association's own price list, and its sales
 * points' menus. For admins and the association's treasurer (credit.manage).
 * Members' money itself is on Admin › Credit; a device's technical side on
 * Settings › Devices.
 *
 * Data: GET /api/v1/admin/sales, /api/v1/admin/credit/items,
 * /api/v1/admin/credit/devices (aeronautics_members/api/credit.py, device_owners.py).
 */
import { Select, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call } from '../../../api/client';
import { AppLink } from '../../../app/AppLink';
import { SalesPointMenus } from '../../../components/devices/SalesPointMenus';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { type ItemChange, PriceListPanel } from '../../../components/PriceList';
import { type SellerSales, SoldItems } from '../../../components/SoldItems';
import { ErrorState, LoadingState } from '../../../components/States';
import { formatEuros } from '../../../lib/format';
import { EVERY_MINUTE, useLiveRefresh } from '../../../lib/live';
import classes from './Credit.module.css';

const salesKey = ['admin', 'sales'] as const;
const associationPricesKey = ['admin', 'credit', 'items'] as const;
const associationDevicesKey = ['admin', 'credit', 'devices'] as const;

const sellerKey = (seller: SellerSales) => seller.team_slug ?? 'association';

function Sellers({ sellers }: { sellers: SellerSales[] }) {
  return (
    <Panel title="Sold by seller" flush>
      <div className={classes.inset}>
        <Table.ScrollContainer minWidth={560} type="native">
          <Table verticalSpacing="sm" aria-label="Sold by seller">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Seller</Table.Th>
                <Table.Th scope="col" className={classes.amount}>
                  Sold, 30 days
                </Table.Th>
                <Table.Th scope="col" className={classes.amount}>
                  Made, 30 days
                </Table.Th>
                <Table.Th scope="col" className={classes.amount}>
                  Sold, ever
                </Table.Th>
                <Table.Th scope="col" className={classes.amount}>
                  Made, ever
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sellers.map((seller) => (
                <Table.Tr key={sellerKey(seller)}>
                  <Table.Td>
                    {seller.team_slug ? (
                      <AppLink to={`/admin/money/${seller.team_slug}`} className={classes.name}>
                        {seller.name}
                      </AppLink>
                    ) : (
                      <span className={classes.name}>{seller.name}</span>
                    )}
                  </Table.Td>
                  <Table.Td className={`${classes.amount} ja-figures`}>{seller.sold_30_days}</Table.Td>
                  <Table.Td className={`${classes.amount} ja-figures`}>
                    {formatEuros(seller.earned_30_days)}
                  </Table.Td>
                  <Table.Td className={`${classes.amount} ja-figures`}>{seller.sold}</Table.Td>
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

function ByItem({ sellers }: { sellers: SellerSales[] }) {
  const [chosen, setChosen] = useState('association');
  const seller = sellers.find((item) => sellerKey(item) === chosen) ?? sellers[0];
  if (!seller) return null;
  return (
    <Panel
      title="Sold by item"
      flush
      actions={
        sellers.length > 1 ? (
          <Select
            aria-label="Seller"
            size="xs"
            data={sellers.map((item) => ({ value: sellerKey(item), label: item.name }))}
            value={sellerKey(seller)}
            allowDeselect={false}
            onChange={(next) => {
              setChosen(next ?? 'association');
            }}
          />
        ) : null
      }
    >
      <div className={classes.inset}>
        <SoldItems seller={seller} label={`Sold by item: ${seller.name}`} />
      </div>
    </Panel>
  );
}

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

/** What the association's sales points offer, from its price list. */
function AssociationMenus() {
  const list = useQuery({
    queryKey: associationDevicesKey,
    queryFn: () => call(api.GET('/api/v1/admin/credit/devices')),
  });
  if (!list.data) return null;
  return (
    <SalesPointMenus
      devices={list.data}
      owner="association"
      queryKey={associationDevicesKey}
      change={(id, body) =>
        call(
          api.PATCH('/api/v1/admin/credit/devices/{device_id}', {
            params: { path: { device_id: id } },
            body,
          }),
        )
      }
    />
  );
}

export function Sales() {
  const sales = useQuery({ queryKey: salesKey, queryFn: () => call(api.GET('/api/v1/admin/sales')) });
  useLiveRefresh(salesKey, EVERY_MINUTE);
  return (
    <>
      <PageHeader
        title="Sales"
        description="What is sold for credit, the association's price list and its sales points."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Sales' }]}
      />
      {sales.isPending ? (
        <LoadingState />
      ) : sales.isError ? (
        <ErrorState error={sales.error} onRetry={() => void sales.refetch()} />
      ) : (
        <Stack gap="lg">
          <Sellers sellers={sales.data.sellers} />
          <ByItem sellers={sales.data.sellers} />
          <AssociationPrices />
          <AssociationMenus />
          <Text size="sm" c="dimmed">
            A team keeps its own price list and menus, on its Sales page. A sale is booked or taken back on a
            person&apos;s credit page.
          </Text>
        </Stack>
      )}
    </>
  );
}
