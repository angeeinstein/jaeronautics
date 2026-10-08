/**
 * Giving and taking a team's roles -- lead and treasurer -- from wherever it
 * is done: the ⋯ menu of a member in the Members list, and the Roles page.
 * Afterwards everything about the team's people is fetched again (the list,
 * the roles, the team's menu). Data: POST|DELETE
 * /api/v1/teams/<slug>/manage/leads and .../treasurer.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { api, call } from '../../../api/client';
import { notifyDone, notifyFailed } from '../../../lib/notify';
import { teamsKey } from '../shared';

export type TeamRole = 'lead' | 'treasurer';

export interface RoleChange {
  give: boolean;
  role: TeamRole;
  userId: number;
  /** Taking away the team's last lead: asked twice before. */
  confirmed?: boolean;
}

const DONE: Record<TeamRole, [string, string]> = {
  lead: ['Lead appointed.', 'No longer a lead.'],
  treasurer: ['Treasurer appointed.', 'No longer treasurer.'],
};

function send(slug: string, { give, role, userId, confirmed = false }: RoleChange) {
  const path = { slug };
  if (role === 'lead') {
    return give
      ? call(api.POST('/api/v1/teams/{slug}/manage/leads', { params: { path }, body: { user_id: userId } }))
      : call(
          api.DELETE('/api/v1/teams/{slug}/manage/leads/{user_id}', {
            params: { path: { slug, user_id: userId }, query: { confirmed } },
          }),
        );
  }
  return give
    ? call(api.POST('/api/v1/teams/{slug}/manage/treasurer', { params: { path }, body: { user_id: userId } }))
    : call(
        api.DELETE('/api/v1/teams/{slug}/manage/treasurer/{user_id}', {
          params: { path: { slug, user_id: userId } },
        }),
      );
}

export function useRoleChange(slug: string, after?: (change: RoleChange) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (change: RoleChange) => send(slug, change),
    onSuccess: async (_roles, change) => {
      notifyDone(DONE[change.role][change.give ? 0 : 1]);
      after?.(change);
      await client.invalidateQueries({ queryKey: [...teamsKey, slug] });
    },
    onError: notifyFailed,
  });
}
