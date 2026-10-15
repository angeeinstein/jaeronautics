/**
 * The admin area's sidebar (docs/frontend-structure.md, section 4). Each
 * entry follows the permission its page requires -- the same ones the pages
 * check on the server -- so nobody is offered a page that would turn them away.
 */
import {
  IconChecklist,
  IconClockHour4,
  IconCoins,
  IconMail,
  IconSpeakerphone,
  IconCurrencyEuro,
  IconFlag,
  IconId,
  IconLayoutDashboard,
  IconListDetails,
  IconScale,
  IconSettings,
  IconTag,
  IconUsers,
} from '@tabler/icons-react';

import { can, canAny, type Me } from '../api/session';
import { compact, type NavItem, type SidebarContent } from './navigation';

/** Settings' sections, each a page of its own. */
function settingsChildren(me: Me): NavItem[] {
  const page = (label: string, slug: string): NavItem => ({ label, to: `/admin/settings/${slug}` });
  // The parts holding secrets only with settings.credentials; health and
  // updates with system.update, backups with system.backup.
  const children = [
    page('General', 'general'),
    page('Notifications', 'notifications'),
    page('Credit', 'credit'),
    page('Mailings', 'mailings'),
  ];
  if (can(me, 'settings.credentials')) {
    children.push(page('Membership fee', 'billing'), page('Forum', 'forum'), page('Mail accounts', 'mail'));
  }
  if (can(me, 'notifications.manage')) children.push(page('Test email', 'test-email'));
  if (can(me, 'devices.manage')) children.push(page('Devices', 'devices'));
  if (can(me, 'system.update')) children.push(page('System health', 'health'), page('Updates', 'updates'));
  if (can(me, 'system.backup')) children.push(page('Backup and restore', 'backup'));
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
          can(me, 'cards.manage') ? { label: 'Cards', to: '/admin/cards', icon: IconId } : null,
          // While it is on for the association, or stays at its readers are kept.
          me.attendance_admin ? { label: 'Attendance', to: '/admin/attendance', icon: IconClockHour4 } : null,
        ],
      },
      {
        label: 'Money',
        items: [
          can(me, 'teams.money') ? { label: 'Money', to: '/admin/money', icon: IconCurrencyEuro } : null,
          me.credit_admin ? { label: 'Credit', to: '/admin/credit', icon: IconCoins } : null,
          me.credit_admin ? { label: 'Sales', to: '/admin/sales', icon: IconTag } : null,
        ],
      },
      {
        label: 'Messages',
        items: [
          can(me, 'messages.receive')
            ? { label: 'Messages', to: '/admin/messages', icon: IconMail, count: me.counts.messages_open }
            : null,
          can(me, 'announcements.send')
            ? { label: 'Announcements', to: '/admin/announcements', icon: IconSpeakerphone }
            : null,
        ],
      },
      {
        label: me.team_labels.plural,
        items: [
          can(me, 'teams.manage')
            ? { label: me.team_labels.plural, to: '/admin/teams', icon: IconFlag }
            : null,
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
