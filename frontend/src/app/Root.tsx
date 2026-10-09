/**
 * Around every page: says the page is in use while somebody uses it
 * (lib/presence.ts), for Settings › Updates.
 */
import { Outlet } from 'react-router';

import { usePresence } from '../lib/presence';

export function Root() {
  usePresence();
  return <Outlet />;
}
