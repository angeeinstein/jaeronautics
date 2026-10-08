/**
 * The overview of all teams: the frame without a sidebar. Each team's own
 * pages have their own frame (TeamLayout).
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
