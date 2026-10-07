/**
 * A team's management: the frame with the team's own sidebar
 * (docs/frontend-structure.md, 5) -- the way back to all teams, the team's
 * mark and name, then its people, its settings, and elsewhere. What appears
 * follows what this person may do in this team.
 */
import {
  IconArrowUpRight,
  IconCoin,
  IconForms,
  IconHistory,
  IconInbox,
  IconLayout,
  IconListCheck,
  IconUserShield,
  IconUsers,
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Outlet, useParams } from 'react-router';

import { ErrorState, LoadingState } from '../components/States';
import { type Manage, manageQuery, may } from '../pages/teams/manage/shared';
import { Frame } from './Frame';
import { compact, type SidebarContent } from './navigation';

export function teamSidebar(team: Manage): SidebarContent {
  const base = `/teams/${team.slug}`;
  const settings = may(team, 'team.edit_settings');
  return {
    back: { label: `All ${team.labels.plural.toLowerCase()}`, to: '/teams' },
    header: { label: team.name, logoUrl: team.logo_url },
    groups: compact([
      {
        label: 'People',
        items: [
          {
            label: 'Applications',
            to: `${base}/manage`,
            icon: IconInbox,
            exact: true,
            count: team.applications,
          },
          { label: 'Members', to: `${base}/manage/members`, icon: IconUsers },
          { label: 'Former members', to: `${base}/manage/former`, icon: IconHistory },
        ],
      },
      {
        label: 'Settings',
        items: [
          settings ? { label: 'Team page', to: `${base}/manage/page`, icon: IconLayout } : null,
          settings ? { label: 'Applying', to: `${base}/manage/applying`, icon: IconForms } : null,
          team.access_list_enabled && may(team, 'team.send_access_list')
            ? { label: 'Access list', to: `${base}/manage/access-list`, icon: IconListCheck }
            : null,
          { label: 'Roles', to: `${base}/manage/roles`, icon: IconUserShield },
        ],
      },
      {
        label: 'Elsewhere',
        items: [
          { label: 'Team page', to: base, icon: IconArrowUpRight, exact: true },
          may(team, 'team.view_money') ? { label: 'Money', to: `${base}/money`, icon: IconCoin } : null,
        ],
      },
    ]),
  };
}

export function TeamManageLayout() {
  const { slug = '' } = useParams();
  const team = useQuery(manageQuery(slug));
  if (!team.data) {
    return (
      <Frame>
        {team.isError ? (
          <ErrorState error={team.error} onRetry={() => void team.refetch()} />
        ) : (
          <LoadingState />
        )}
      </Frame>
    );
  }
  const content = teamSidebar(team.data);
  const notices = team.data.has_lead_in_force
    ? []
    : [
        {
          tone: 'warning' as const,
          message: 'This team has no active lead.',
          link_url: null,
          link_label: null,
        },
      ];
  return (
    <Frame sidebar={() => content} notices={() => notices}>
      <Outlet />
    </Frame>
  );
}
