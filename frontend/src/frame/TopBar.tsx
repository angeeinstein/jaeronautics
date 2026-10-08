/**
 * The top bar on every page while signed in: the menu button where a page
 * has a sidebar (it sits right above it), the logo, the areas (the Forum for a
 * member, Teams while teams are on), then at the right, for whoever may
 * administer something, the gear into the admin area -- as on many sites, the
 * "back office" behind an icon beside the person -- and the person's menu:
 * their picture and name, the way to their account. On a phone the logo
 * shrinks to the mark.
 */
import { ActionIcon, Box, Burger, Group, Tooltip, VisuallyHidden } from '@mantine/core';
import { IconSettings } from '@tabler/icons-react';
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

/** Into the admin area, marked while one is in it. */
function AdminGear() {
  const inAdmin = /^\/admin(\/|$)/.test(useLocation().pathname);
  return (
    <Tooltip label="Admin" openDelay={300}>
      <ActionIcon
        component={AppLink}
        to="/admin"
        variant="subtle"
        size="lg"
        className={classes.gear}
        aria-label="Admin"
        aria-current={inAdmin ? 'page' : undefined}
      >
        <IconSettings size={20} stroke={1.6} />
      </ActionIcon>
    </Tooltip>
  );
}

export function TopBar({ me, menu }: TopBarProps) {
  const current = areaOf(useLocation().pathname);
  // My Account is the menu at the right, and the logo; the admin area is the gear.
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
                // The forum in a tab of its own, once /forum would open it; while
                // something is still missing, the page saying what stays here.
                <a
                  key={entry.area}
                  href={entry.to}
                  className={classes.area}
                  aria-current={current === entry.area ? 'page' : undefined}
                  {...(me.forum_ready ? { target: '_blank', rel: 'noopener' } : {})}
                >
                  {entry.label}
                  {me.forum_ready ? <VisuallyHidden> (opens in a new tab)</VisuallyHidden> : null}
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
      <Group gap="xs" wrap="nowrap">
        {me.admin_area ? <AdminGear /> : null}
        <UserMenu me={me} />
      </Group>
    </Group>
  );
}
