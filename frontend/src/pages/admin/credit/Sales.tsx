/**
 * Admin › Sales (docs/credit-plan.md, "Where it is shown"): what is sold for
 * credit by seller (the association and each team); then, as tabs, the
 * association's own price list -- with how many of each item sold and what
 * it made -- and its sales points' menus. For admins and the association's treasurer (credit.manage).
 * Members' money itself is on Admin › Credit; a device's technical side on
 * Settings › Devices.
 *
 * Data: GET /api/v1/admin/sales, /api/v1/admin/credit/items,
 * /api/v1/admin/credit/devices (aeronautics_members/api/credit.py, device_owners.py).
 */
import { Stack, Table, Text } from '@mantine/core';
import { IconChartBar } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';

import { api, call, type Schemas } from '../../../api/client';
import { AppLink } from '../../../app/AppLink';
import { SalesPointMenus } from '../../../components/devices/SalesPointMenus';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { type ItemChange, PriceListPanel } from '../../../components/PriceList';
import { SalesTabs } from '../../../components/SalesTabs';
import { ErrorState, LoadingState } from '../../../components/States';
import { formatEuros } from '../../../lib/format';
import { EVERY_MINUTE, useLiveRefresh } from '../../../lib/live';
import { menuFields } from '../settings/deviceParts';
import classes from './Credit.module.css';

type SellerSales = Schemas['SellerSalesOut'];

const salesKey = ['admin', 'sales'] as const;
const associationPricesKey = ['admin', 'credit', 'items'] as const;
const associationDevicesKey = ['admin', 'credit', 'devices'] as const;

const sellerKey = (seller: SellerSales) => seller.team_slug ?? 'association';

function Sellers({ sellers }: { sellers: SellerSales[] }) {
  return (
    <Panel title="Sold by seller" icon={IconChartBar} flush>
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

export function Sales() {
  const sales = useQuery({ queryKey: salesKey, queryFn: () => call(api.GET('/api/v1/admin/sales')) });
  const prices = useQuery({
    queryKey: associationPricesKey,
    queryFn: () => call(api.GET('/api/v1/admin/credit/items')),
  });
  const devices = useQuery({
    queryKey: associationDevicesKey,
    queryFn: () => call(api.GET('/api/v1/admin/credit/devices')),
  });
  useLiveRefresh(salesKey, EVERY_MINUTE);
  const association = sales.data?.sellers.find((seller) => seller.team_slug === null);
  const hasPoints = devices.data?.items.some((device) => menuFields(device.fields).length) ?? false;
  return (
    <>
      <PageHeader
        title="Sales"
        description="What is sold for credit, the association's price list and its sales points."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Sales' }]}
      />
      {sales.isPending || prices.isPending ? (
        <LoadingState />
      ) : sales.isError ? (
        <ErrorState error={sales.error} onRetry={() => void sales.refetch()} />
      ) : prices.isError ? (
        <ErrorState error={prices.error} onRetry={() => void prices.refetch()} />
      ) : (
        <Stack gap="lg">
          <Sellers sellers={sales.data.sellers} />
          <SalesTabs
            priceList={
              <PriceListPanel
                title="The association's price list"
                list={prices.data}
                queryKey={associationPricesKey}
                sales={association}
                salesKey={salesKey}
                add={(body) => call(api.POST('/api/v1/admin/credit/items', { body }))}
                change={(id: number, body: ItemChange) =>
                  call(
                    api.PUT('/api/v1/admin/credit/items/{item_id}', {
                      params: { path: { item_id: id } },
                      body,
                    }),
                  )
                }
                remove={(id: number) =>
                  call(
                    api.DELETE('/api/v1/admin/credit/items/{item_id}', { params: { path: { item_id: id } } }),
                  )
                }
              />
            }
            salesPoints={
              hasPoints && devices.data ? (
                <SalesPointMenus
                  devices={devices.data}
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
              ) : null
            }
          />
          <Text size="sm" c="dimmed">
            A team keeps its own price list and menus, on its Sales page. A sale is booked or taken back on a
            person&apos;s credit page.
          </Text>
        </Stack>
      )}
    </>
  );
}
