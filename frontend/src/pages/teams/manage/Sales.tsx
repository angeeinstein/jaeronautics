/**
 * A team's Sales page (docs/credit-plan.md, "Selling", "Where it is shown"):
 * what it sold for credit, by item -- how many and what
 * each made -- its price list, and its sales points' menus, chosen from that
 * list. For its leads and treasurer. What it sold counts towards what the
 * team is owed (its Money page); a device's name and switch are on Devices.
 *
 * Data: GET /api/v1/teams/<slug>/manage/sales, GET|POST .../manage/prices,
 * PUT .../<id>, the team's devices (.../manage/devices).
 */
import { Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { api, call } from '../../../api/client';
import { SalesPointMenus } from '../../../components/devices/SalesPointMenus';
import { Panel } from '../../../components/Panel';
import { type ItemChange, PriceListPanel } from '../../../components/PriceList';
import { SoldItems } from '../../../components/SoldItems';
import { ErrorState, LoadingState } from '../../../components/States';
import { ManageHeader, may, useManage, useSlug } from './shared';

function Menus({ slug }: { slug: string }) {
  const queryKey = ['teams', slug, 'devices'] as const;
  const list = useQuery({
    queryKey,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/devices', { params: { path: { slug } } })),
  });
  if (!list.data) return null;
  return (
    <SalesPointMenus
      devices={list.data}
      owner={slug}
      queryKey={queryKey}
      change={(id, body) =>
        call(
          api.PATCH('/api/v1/teams/{slug}/manage/devices/{device_id}', {
            params: { path: { slug, device_id: id } },
            body,
          }),
        )
      }
    />
  );
}

export function Sales() {
  const slug = useSlug();
  const manage = useManage().data;
  const path = { slug };
  const queryKey = ['teams', slug, 'prices'] as const;
  const list = useQuery({
    queryKey,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/prices', { params: { path } })),
  });
  const sold = useQuery({
    queryKey: ['teams', slug, 'sales'],
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/sales', { params: { path } })),
  });
  return (
    <>
      <ManageHeader title="Sales" description="What the team sells for credit, and what it sold." />
      {list.isPending ? (
        <LoadingState />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : (
        <Stack gap="lg">
          {sold.data ? (
            <Panel title="Sold by item" flush>
              <SoldItems seller={sold.data} label="Sold by item" />
            </Panel>
          ) : null}
          <PriceListPanel
            title="Price list"
            list={list.data}
            queryKey={queryKey}
            add={(body) => call(api.POST('/api/v1/teams/{slug}/manage/prices', { params: { path }, body }))}
            change={(id: number, body: ItemChange) =>
              call(
                api.PUT('/api/v1/teams/{slug}/manage/prices/{item_id}', {
                  params: { path: { slug, item_id: id } },
                  body,
                }),
              )
            }
          />
          {may(manage, 'team.devices') ? <Menus slug={slug} /> : null}
          <Text size="sm" c="dimmed">
            What is sold counts towards what the team is owed and is transferred with the next payout. A new
            price counts from the next sale.
          </Text>
        </Stack>
      )}
    </>
  );
}
