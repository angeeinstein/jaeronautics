/**
 * A team's Sales page (docs/credit-plan.md, "Selling", "Where it is shown"):
 * as tabs, its price list -- with how many of each item sold and what it
 * made -- and its sales points' menus, chosen from that list. For its leads and treasurer. What it sold counts towards what the
 * team is owed (its Money page); a device's name and switch are on Devices.
 *
 * Data: GET /api/v1/teams/<slug>/manage/sales, GET|POST .../manage/prices,
 * PUT .../<id>, the team's devices (.../manage/devices).
 */
import { Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { api, call } from '../../../api/client';
import { SalesPointMenus } from '../../../components/devices/SalesPointMenus';
import { type ItemChange, PriceListPanel } from '../../../components/PriceList';
import { SalesTabs } from '../../../components/SalesTabs';
import { ErrorState, LoadingState } from '../../../components/States';
import { menuFields } from '../../admin/settings/deviceParts';
import { ManageHeader, may, useManage, useSlug } from './shared';

export function Sales() {
  const slug = useSlug();
  const manage = useManage().data;
  const path = { slug };
  const queryKey = ['teams', slug, 'prices'] as const;
  const salesKey = ['teams', slug, 'sales'] as const;
  const devicesKey = ['teams', slug, 'devices'] as const;
  const list = useQuery({
    queryKey,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/prices', { params: { path } })),
  });
  const sold = useQuery({
    queryKey: salesKey,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/sales', { params: { path } })),
  });
  const devices = useQuery({
    queryKey: devicesKey,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/devices', { params: { path } })),
    enabled: may(manage, 'team.devices'),
  });
  const hasPoints = devices.data?.items.some((device) => menuFields(device.fields).length) ?? false;
  return (
    <>
      <ManageHeader title="Sales" description="What the team sells for credit, and what it sold." />
      {list.isPending ? (
        <LoadingState />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : (
        <Stack gap="lg">
          <SalesTabs
            priceList={
              <PriceListPanel
                title="Price list"
                list={list.data}
                queryKey={queryKey}
                sales={sold.data}
                salesKey={salesKey}
                add={(body) =>
                  call(api.POST('/api/v1/teams/{slug}/manage/prices', { params: { path }, body }))
                }
                change={(id: number, body: ItemChange) =>
                  call(
                    api.PUT('/api/v1/teams/{slug}/manage/prices/{item_id}', {
                      params: { path: { slug, item_id: id } },
                      body,
                    }),
                  )
                }
                remove={(id: number) =>
                  call(
                    api.DELETE('/api/v1/teams/{slug}/manage/prices/{item_id}', {
                      params: { path: { slug, item_id: id } },
                    }),
                  )
                }
              />
            }
            salesPoints={
              hasPoints && devices.data ? (
                <SalesPointMenus
                  devices={devices.data}
                  owner={slug}
                  queryKey={devicesKey}
                  change={(id, body) =>
                    call(
                      api.PATCH('/api/v1/teams/{slug}/manage/devices/{device_id}', {
                        params: { path: { slug, device_id: id } },
                        body,
                      }),
                    )
                  }
                />
              ) : null
            }
          />
          <Text size="sm" c="dimmed">
            What is sold counts towards what the team is owed and is transferred with the next payout. A new
            price counts from the next sale.
          </Text>
        </Stack>
      )}
    </>
  );
}
