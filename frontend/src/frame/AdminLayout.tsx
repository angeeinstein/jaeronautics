/**
 * The admin area: the frame with the admin sidebar and the admin notices
 * (a test setting left on, background jobs paused) above every page.
 */
import { Outlet } from 'react-router';

import { adminSidebar } from './adminNavigation';
import { Frame } from './Frame';

export function AdminLayout() {
  return (
    <Frame sidebar={adminSidebar} notices={(me) => me.admin_notices}>
      <Outlet />
    </Frame>
  );
}
