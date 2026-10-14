/**
 * A team's devices (docs/devices-plan.md, "Owners"): the readers an admin
 * gave the team, run here by its leads and treasurer -- their names and
 * places, on or off, and what they reported lately. What a sales point sells
 * is chosen on the Sales page, with the price list. Data: GET /api/v1/teams/<slug>/manage/devices,
 * PATCH .../<id> (aeronautics_members/api/device_owners.py).
 */
import { useQuery } from '@tanstack/react-query';

import { api, call } from '../../../api/client';
import { OwnedDeviceList } from '../../../components/devices/OwnedDevices';
import { ErrorState, LoadingState } from '../../../components/States';
import { EVERY_30_SECONDS, useLiveRefresh } from '../../../lib/live';
import { ManageHeader, useSlug } from './shared';

export function Devices() {
  const slug = useSlug();
  const queryKey = ['teams', slug, 'devices'] as const;
  const list = useQuery({
    queryKey,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/devices', { params: { path: { slug } } })),
  });
  useLiveRefresh(queryKey, EVERY_30_SECONDS);
  return (
    <>
      <ManageHeader title="Devices" description="The team's card readers, and how they are doing." />
      {list.isPending ? (
        <LoadingState />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : (
        <OwnedDeviceList
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
      )}
    </>
  );
}
