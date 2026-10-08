/**
 * My Account: the frame with its side menu (docs/frontend-structure.md, 6) --
 * the overview, then each part on a page of its own: the profile, the
 * membership and its billing, the forum and one's picture, one's data. More
 * parts (credit, say) are an entry here and a page. An account without a
 * membership has the overview and its data only. Its pages keep the narrow
 * centred column.
 */
import {
  IconCreditCard,
  IconLayoutDashboard,
  IconMessages,
  IconShieldLock,
  IconUserCircle,
} from '@tabler/icons-react';
import { Outlet } from 'react-router';

import type { Me } from '../api/session';
import { Frame } from './Frame';
import { compact, type SidebarContent } from './navigation';

export function accountSidebar(me: Me): SidebarContent {
  const member = me.forum_area;
  return {
    groups: compact([
      {
        label: 'My account',
        items: [
          { label: 'Overview', to: '/account', icon: IconLayoutDashboard, exact: true },
          member ? { label: 'Profile', to: '/account/profile', icon: IconUserCircle } : null,
          member
            ? { label: 'Membership and billing', to: '/account/membership', icon: IconCreditCard }
            : null,
          member ? { label: 'Forum and picture', to: '/account/forum', icon: IconMessages } : null,
          { label: 'Privacy and data', to: '/account/data', icon: IconShieldLock },
        ],
      },
    ]),
  };
}

export function AccountLayout() {
  return (
    <Frame narrow sidebar={accountSidebar}>
      <Outlet />
    </Frame>
  );
}
