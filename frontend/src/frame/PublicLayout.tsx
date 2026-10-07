/**
 * The frame of the pages anybody may open -- signing in, a new password, the
 * legal texts, joining: the top bar with the logo and the way in (Sign in and
 * Join, or My Account for somebody signed in), a narrow centred column (or the
 * full one), and the footer. Needs nobody signed in, so it asks only GET /api/v1/session.
 */
import { AppShell, Box, Button, Group } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Outlet, useLocation } from 'react-router';

import { api, call } from '../api/client';
import { AppLink } from '../app/AppLink';
import logo from '../assets/logo.svg';
import mark from '../assets/mark.svg';
import { Footer } from './Footer';
import classes from './Frame.module.css';

export const sessionQuery = {
  queryKey: ['session'] as const,
  queryFn: () => call(api.GET('/api/v1/session')),
  staleTime: 60_000,
};

function WayIn() {
  const session = useQuery(sessionQuery);
  const { pathname } = useLocation();
  if (!session.data) return null;
  if (session.data.signed_in) {
    return (
      <Button component={AppLink} to="/account" size="sm">
        My Account
      </Button>
    );
  }
  return (
    <Group gap="xs" wrap="nowrap">
      {pathname === '/login' ? null : (
        <Button component={AppLink} to="/login" size="sm" variant="default">
          Sign in
        </Button>
      )}
      <Button component={AppLink} to="/join" size="sm">
        Join
      </Button>
    </Group>
  );
}

/** ``wide``: the full column, for a legal text with its contents beside it. */
export function PublicLayout({ wide = false }: { wide?: boolean }) {
  const column = wide ? classes.content : classes.contentNarrow;
  return (
    <AppShell header={{ height: 'var(--ja-topbar-height)' }} padding={0}>
      <AppShell.Header withBorder={false}>
        <Group className={classes.topBar} h="100%" px="md" gap="sm" wrap="nowrap" justify="space-between">
          <AppLink to="/" aria-label="Joanneum Aeronautics, start page">
            <Box component="img" src={logo} alt="" className={classes.logo} visibleFrom="sm" />
            <Box component="img" src={mark} alt="" className={classes.mark} hiddenFrom="sm" />
          </AppLink>
          <WayIn />
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <div className={classes.main} id="content">
          <div className={column}>
            <Outlet />
          </div>
        </div>
      </AppShell.Main>
      <div className={classes.footerShell}>
        <div className={column}>
          <Footer />
        </div>
      </div>
    </AppShell>
  );
}
