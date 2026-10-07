/**
 * The top bar on every page while signed in: the logo, the areas (My
 * Account, Teams while teams are on, Admin with access to it), the person's
 * menu. On a phone the logo shrinks to the mark and, where the area has a
 * sidebar, a menu button opens it as a drawer.
 */
import { Box, Burger, Group } from '@mantine/core';
import { useLocation } from 'react-router';

import { AppLink } from '../app/AppLink';
import type { Me } from '../api/session';
import logo from '../assets/logo.svg';
import mark from '../assets/mark.svg';
import classes from './Frame.module.css';
import { UserMenu } from './UserMenu';

export type Area = 'account' | 'teams' | 'admin';

/** Which area an address belongs to, for marking it in the bar. */
export function areaOf(pathname: string): Area | null {
  if (pathname === '/admin' || pathname.startsWith('/admin/')) return 'admin';
  if (pathname === '/teams' || pathname.startsWith('/teams/')) return 'teams';
  if (/^\/(account|forum|change-password)(\/|$)/.test(pathname)) return 'account';
  return null;
}

interface TopBarProps {
  me: Me;
  /** Given where the area has a sidebar: opens it as a drawer on a phone. */
  menu?: { opened: boolean; toggle: () => void };
}

export function TopBar({ me, menu }: TopBarProps) {
  const current = areaOf(useLocation().pathname);
  const areas: { area: Area; label: string; to: string; shown: boolean }[] = [
    { area: 'account', label: 'My Account', to: '/account', shown: true },
    { area: 'teams', label: me.team_labels.plural, to: '/teams', shown: me.teams_area },
    { area: 'admin', label: 'Admin', to: '/admin', shown: me.admin_area },
  ];

  return (
    <Group
      className={classes.topBar}
      h="var(--ja-topbar-height)"
      px="md"
      gap="sm"
      wrap="nowrap"
      justify="space-between"
    >
      <Group gap="sm" wrap="nowrap" h="100%">
        {menu ? (
          <Burger
            opened={menu.opened}
            onClick={menu.toggle}
            hiddenFrom="sm"
            size="sm"
            color="var(--ja-text)"
            aria-label={menu.opened ? 'Close the menu' : 'Open the menu'}
          />
        ) : null}
        <AppLink to="/account" aria-label="Joanneum Aeronautics, my account">
          <Box component="img" src={logo} alt="" className={classes.logo} visibleFrom="sm" />
          <Box component="img" src={mark} alt="" className={classes.mark} hiddenFrom="sm" />
        </AppLink>
        <Group component="nav" aria-label="Areas" gap={0} h="100%" wrap="nowrap" ml={{ base: 0, sm: 'lg' }}>
          {areas
            .filter((entry) => entry.shown)
            .map((entry) => (
              <AppLink
                key={entry.area}
                to={entry.to}
                className={classes.area}
                aria-current={current === entry.area ? 'page' : undefined}
              >
                {entry.label}
              </AppLink>
            ))}
        </Group>
      </Group>
      <UserMenu me={me} />
    </Group>
  );
}
