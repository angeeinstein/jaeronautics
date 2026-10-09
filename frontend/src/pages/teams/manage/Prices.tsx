/**
 * A team's price list (docs/credit-plan.md, "Selling"): what it sells for
 * credit -- a beer from its fridge -- and at what price. For its leads and
 * treasurer. What it sells counts towards what the team is owed (its Money
 * page). Data: GET|POST /api/v1/teams/<slug>/manage/prices, PUT .../<id>.
 */
import { Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { api, call } from '../../../api/client';
import { type ItemChange, PriceListPanel } from '../../../components/PriceList';
import { ErrorState, LoadingState } from '../../../components/States';
import { ManageHeader, useSlug } from './shared';

export function Prices() {
  const slug = useSlug();
  const path = { slug };
  const queryKey = ['teams', slug, 'prices'] as const;
  const list = useQuery({
    queryKey,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/prices', { params: { path } })),
  });
  return (
    <>
      <ManageHeader title="Prices" description="What the team sells for credit, and at what price." />
      {list.isPending ? (
        <LoadingState />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : (
        <Stack gap="lg">
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
          <Text size="sm" c="dimmed">
            What is sold counts towards what the team is owed and is transferred with the next payout. A new
            price counts from the next sale.
          </Text>
        </Stack>
      )}
    </>
  );
}
