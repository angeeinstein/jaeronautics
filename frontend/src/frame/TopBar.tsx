/**
 * The top bar on every page while signed in: the menu button, the logo, the
 * areas (the Forum for a member, Teams while teams are on), the person's menu
 * -- their picture and name, the way to their account. On a phone the logo
 * shrinks to the mark.
 *
 * The menu button sits at the far left, above where the menu it opens comes
 * in. For whoever may administer something it is always there: the way into
 * the admin area, which has no area link of its own (Frame decides what the
 * button opens). Without that, it is there on a phone where a page has a
 * sidebar, to open it.
 */
import { Box, Burger, Group } from '@mantine/core';
import { useLocation } from 'react-router';

import { AppLink } from '../app/AppLink';
import type { Me } from '../api/session';
import logo from '../assets/logo.svg';
import mark from '../assets/mark.svg';
import classes from './Frame.module.css';
import { UserMenu } from './UserMenu';

export type Area = 'forum' | 'teams';

/** Which area an address belongs to, for marking it in the bar. */
export function areaOf(pathname: string): Area | null {
  if (pathname === '/teams' || pathname.startsWith('/teams/')) return 'teams';
  if (pathname === '/forum' || pathname.startsWith('/forum/')) return 'forum';
  return null;
}

/** What the menu button does at one screen size: its look, its name, and what a click does. */
export interface MenuButton {
  /** Drawn as a cross: a drawer it closes is open. */
  opened: boolean;
  label: string;
  toggle: () => void;
}

interface TopBarProps {
  me: Me;
  /** The menu button on a phone, and on a wider screen; none where there is nothing to open. */
  menu?: { phone?: MenuButton; wide?: MenuButton };
}

function MenuBurger({ button, size }: { button: MenuButton; size: 'phone' | 'wide' }) {
  return (
    <Burger
      opened={button.opened}
      onClick={button.toggle}
      hiddenFrom={size === 'phone' ? 'sm' : undefined}
      visibleFrom={size === 'wide' ? 'sm' : undefined}
      size="sm"
      color="var(--ja-text)"
      aria-label={button.label}
    />
  );
}

export function TopBar({ me, menu }: TopBarProps) {
  const current = areaOf(useLocation().pathname);
  // My Account is the menu at the right, and the logo; the admin area is the menu button.
  const all: { area: Area; label: string; to: string; shown: boolean; server?: boolean }[] = [
    // The server's own address: it signs a member into the forum, or says what is still missing.
    { area: 'forum', label: 'Forum', to: '/forum', shown: me.forum_area, server: true },
    { area: 'teams', label: me.team_labels.plural, to: '/teams', shown: me.teams_area },
  ];
  const areas = all.filter((entry) => entry.shown);

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
        {menu?.phone ? <MenuBurger button={menu.phone} size="phone" /> : null}
        {menu?.wide ? <MenuBurger button={menu.wide} size="wide" /> : null}
        <AppLink to="/account" aria-label="Joanneum Aeronautics, my account">
          <Box component="img" src={logo} alt="" className={classes.logo} visibleFrom="sm" />
          <Box component="img" src={mark} alt="" className={classes.mark} hiddenFrom="sm" />
        </AppLink>
        {areas.length ? (
          <Group component="nav" aria-label="Areas" gap={0} h="100%" wrap="nowrap" ml={{ base: 0, sm: 'lg' }}>
            {areas.map((entry) =>
              entry.server ? (
                <a
                  key={entry.area}
                  href={entry.to}
                  className={classes.area}
                  aria-current={current === entry.area ? 'page' : undefined}
                >
                  {entry.label}
                </a>
              ) : (
                <AppLink
                  key={entry.area}
                  to={entry.to}
                  className={classes.area}
                  aria-current={current === entry.area ? 'page' : undefined}
                >
                  {entry.label}
                </AppLink>
              ),
            )}
          </Group>
        ) : null}
      </Group>
      <UserMenu me={me} />
    </Group>
  );
}
