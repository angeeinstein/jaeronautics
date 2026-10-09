/**
 * A team's own area: every page of one team -- its overview, what it is
 * about, its people, money and settings -- in the frame with the team's side
 * menu (docs/frontend-structure.md, 5): the way back to all teams, the team's
 * mark and name, then what this person may open in it. Somebody not in the
 * team, who runs nothing in it, gets the pages without the menu: what the
 * team is about, and joining it.
 *
 * Without the team (the association's treasurer looking at the money of a
 * team that is archived, say) the pages still show, in the plain frame.
 */
import {
  IconAdjustments,
  IconArchive,
  IconCoin,
  IconForms,
  IconHistory,
  IconInbox,
  IconInfoCircle,
  IconLayout,
  IconLayoutDashboard,
  IconListCheck,
  IconReceipt,
  IconTag,
  IconUserShield,
  IconUsers,
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Outlet, useParams } from 'react-router';

import type { Schemas } from '../api/client';
import { LoadingState } from '../components/States';
import { useLiveRefresh } from '../lib/live';
import { teamQuery } from '../pages/teams/shared';
import { Frame } from './Frame';
import { compact, type SidebarContent } from './navigation';

type Team = Schemas['TeamPageOut'];
type TeamPermission = Team['permissions'][number];

function may(team: Team, permission: TeamPermission) {
  return team.permissions.includes(permission);
}

/** Whether this person has the team's menu: they are in it, or run some part of it. */
export function hasTeamMenu(team: Team) {
  return team.sees_team_page || team.permissions.length > 0;
}

export function teamSidebar(team: Team): SidebarContent {
  const base = `/teams/${team.slug}`;
  const settings = may(team, 'team.edit_settings');
  // The team's treasurer sees its money, nothing about its people.
  const people = may(team, 'team.view_members');
  return {
    back: { label: `All ${team.labels.plural.toLowerCase()}`, to: '/teams' },
    header: { label: team.name, logoUrl: team.logo_url },
    groups: compact([
      {
        label: team.labels.singular,
        items: [
          team.sees_team_page
            ? { label: 'Overview', to: base, icon: IconLayoutDashboard, exact: true }
            : null,
          {
            label: `About the ${team.labels.singular.toLowerCase()}`,
            to: `${base}/about`,
            icon: IconInfoCircle,
          },
        ],
      },
      {
        label: 'People',
        items: [
          people
            ? {
                label: 'Applications',
                to: `${base}/manage`,
                icon: IconInbox,
                exact: true,
                count: team.applications_waiting ?? 0,
              }
            : null,
          people ? { label: 'Members', to: `${base}/manage/members`, icon: IconUsers } : null,
          people ? { label: 'Former members', to: `${base}/manage/former`, icon: IconHistory } : null,
        ],
      },
      {
        label: 'Money',
        items: [
          may(team, 'team.view_money') ? { label: 'Money', to: `${base}/money`, icon: IconCoin } : null,
          team.sells_for_credit && may(team, 'team.edit_prices')
            ? { label: 'Prices', to: `${base}/manage/prices`, icon: IconTag }
            : null,
        ],
      },
      {
        label: 'Settings',
        items: [
          settings
            ? { label: `${team.labels.singular} page`, to: `${base}/manage/page`, icon: IconLayout }
            : null,
          settings ? { label: 'Joining', to: `${base}/manage/applying`, icon: IconForms } : null,
          team.access_list_enabled && may(team, 'team.send_access_list')
            ? { label: 'Access list', to: `${base}/manage/access-list`, icon: IconListCheck }
            : null,
          people ? { label: 'Roles', to: `${base}/manage/roles`, icon: IconUserShield } : null,
        ],
      },
      {
        // Only site admins: what is the association's to decide about the team.
        label: 'Admin',
        items: team.administers
          ? [
              { label: 'Details', to: `${base}/manage/details`, icon: IconAdjustments },
              { label: 'Fee', to: `${base}/manage/fee`, icon: IconReceipt },
              { label: 'Archive', to: `${base}/manage/archive`, icon: IconArchive },
            ]
          : [],
      },
    ]),
  };
}

const LEAD_MISSING: Schemas['Notice'] = {
  tone: 'warning',
  message: 'This team has no active lead.',
  link_url: null,
  link_label: null,
};

export function TeamLayout() {
  const { slug = '' } = useParams();
  const team = useQuery({ ...teamQuery(slug), retry: false });
  // Applications coming in show in the menu's count without a reload.
  useLiveRefresh(
    teamQuery(slug).queryKey,
    60_000,
    team.data ? team.data.applications_waiting !== null : false,
  );
  if (team.isPending) {
    return (
      <Frame>
        <LoadingState />
      </Frame>
    );
  }
  if (team.isError || !hasTeamMenu(team.data)) {
    return (
      <Frame>
        <Outlet />
      </Frame>
    );
  }
  const data = team.data;
  return (
    <Frame sidebar={() => teamSidebar(data)} notices={() => (data.lead_missing ? [LEAD_MISSING] : [])}>
      <Outlet />
    </Frame>
  );
}
