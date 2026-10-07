/**
 * The teams area for members: the frame without a sidebar -- the overview, a
 * team's page, what a team is about, its rules. A team's management has the
 * team's own sidebar.
 */
import { Outlet } from 'react-router';

import { Frame } from './Frame';

export function TeamsLayout() {
  return (
    <Frame>
      <Outlet />
    </Frame>
  );
}
