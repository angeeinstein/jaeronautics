/**
 * The admin area: the frame with the admin sidebar and the admin notices
 * (a test setting left on, background jobs paused) above every page. The
 * sidebar's counts of what waits stay current (lib/live.ts).
 */
import { Outlet } from 'react-router';

import { meQuery } from '../api/session';
import { EVERY_30_SECONDS, useLiveRefresh } from '../lib/live';

import { adminSidebar } from './adminNavigation';
import { Frame } from './Frame';

export function AdminLayout() {
  useLiveRefresh(meQuery.queryKey, EVERY_30_SECONDS);
  return (
    <Frame sidebar={adminSidebar} notices={(me) => me.admin_notices}>
      <Outlet />
    </Frame>
  );
}
