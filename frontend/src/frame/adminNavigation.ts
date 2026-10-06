/**
 * The admin area's sidebar (docs/frontend-structure.md, section 4). Each
 * entry follows the permission its page requires -- the same ones the pages
 * check on the server -- so nobody is offered a page that would turn them away.
 */
import {
  IconChecklist,
  IconCurrencyEuro,
  IconFlag,
  IconLayoutDashboard,
  IconListDetails,
  IconScale,
  IconSettings,
  IconUsers,
} from '@tabler/icons-react';

import { can, canAny, type Me } from '../api/session';
import { compact, type NavItem, type SidebarContent } from './navigation';

/**
 * Settings' sections: pages of their own as they move to the new front end
 * (step 4.8), anchors on the old page until then.
 */
function settingsChildren(me: Me): NavItem[] {
  const section = (label: string, anchor: string): NavItem => ({ label, to: `/admin/settings#${anchor}` });
  const page = (label: string, slug: string): NavItem => ({ label, to: `/admin/settings/${slug}` });
  // As today's page shows them: the parts holding secrets only with
  // settings.credentials, Maintenance only with system.update.
  const children = [page('General', 'general'), page('Notifications', 'notifications')];
  if (can(me, 'settings.credentials')) {
    children.push(page('Membership fee', 'billing'), page('Forum', 'forum'), page('Mail accounts', 'mail'));
  }
  if (can(me, 'notifications.manage')) children.push(page('Test email', 'test-email'));
  if (can(me, 'system.update')) children.push(section('Maintenance', 'settings-maintenance'));
  return children;
}

export function adminSidebar(me: Me): SidebarContent {
  return {
    back: { label: 'Back to the portal', to: '/account' },
    groups: compact([
      {
        label: 'Overview',
        items: [{ label: 'Dashboard', to: '/admin', icon: IconLayoutDashboard, exact: true }],
      },
      {
        label: 'People',
        items: [
          can(me, 'accounts.view') ? { label: 'Accounts', to: '/admin/accounts', icon: IconUsers } : null,
          canAny(me, 'approvals.review', 'forum.moderate')
            ? {
                label: 'Reviews',
                to: '/admin/reviews',
                icon: IconChecklist,
                count: me.counts.reviews_waiting,
              }
            : null,
        ],
      },
      {
        label: me.team_labels.plural,
        items: [
          can(me, 'teams.manage')
            ? { label: me.team_labels.plural, to: '/admin/teams', icon: IconFlag }
            : null,
          can(me, 'teams.money') ? { label: 'Money', to: '/admin/money', icon: IconCurrencyEuro } : null,
        ],
      },
      {
        label: 'System',
        items: [
          can(me, 'logs.view') ? { label: 'Logs', to: '/admin/logs', icon: IconListDetails } : null,
          can(me, 'settings.general') ? { label: 'Legal texts', to: '/admin/legal', icon: IconScale } : null,
          can(me, 'settings.general')
            ? { label: 'Settings', to: '/admin/settings', icon: IconSettings, children: settingsChildren(me) }
            : null,
        ],
      },
    ]),
  };
}
