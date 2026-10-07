/**
 * My Account: the frame without a sidebar, in the narrow centred column of
 * the member pages (docs/frontend-structure.md, 3).
 */
import { Outlet } from 'react-router';

import { Frame } from './Frame';

export function AccountLayout() {
  return (
    <Frame narrow>
      <Outlet />
    </Frame>
  );
}
