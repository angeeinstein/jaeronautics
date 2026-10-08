/**
 * The page around every signed-in page: the top bar, the area's sidebar
 * where it has one (a drawer on a phone), the area's notices, the page, and
 * the footer.
 *
 * The menu button at the far left: in the admin area it folds the sidebar
 * away and back; anywhere else it slides the admin menu in from the left, for
 * whoever may administer something -- the admin area has no link of its own
 * in the bar. On a phone it opens the page's own sidebar (admin, or a team's
 * management), else the admin menu.
 * Waits for the person (``/me``) once; a page inside never shows without it.
 */
import { Alert, AppShell, Button, Center, Drawer, Group, Loader, Stack, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import type { ReactNode } from 'react';

import { AppLink } from '../app/AppLink';
import type { Schemas } from '../api/client';
import { type Me, useMe } from '../api/session';
import { ErrorState } from '../components/States';
import { adminSidebar } from './adminNavigation';
import { Footer } from './Footer';
import classes from './Frame.module.css';
import type { SidebarContent } from './navigation';
import { Sidebar } from './Sidebar';
import { TestServerBar } from './TestServerBar';
import { type MenuButton, TopBar } from './TopBar';

const NOTICE_COLOURS = { info: 'brand', warning: 'amber', danger: 'red' } as const;

function Notices({ notices }: { notices: Schemas['Notice'][] }) {
  if (!notices.length) return null;
  return (
    <Stack gap="xs" mb="md">
      {notices.map((notice) => (
        <Alert key={notice.message} color={NOTICE_COLOURS[notice.tone]} variant="light" role="status">
          <Group justify="space-between" gap="sm">
            <Text size="sm">{notice.message}</Text>
            {notice.link_url ? (
              <Button component={AppLink} to={notice.link_url} size="xs" variant="default">
                {notice.link_label}
              </Button>
            ) : null}
          </Group>
        </Alert>
      ))}
    </Stack>
  );
}

/** A sidebar in from the left, named for whoever cannot see it; a page chosen in it closes it. */
function MenuDrawer({
  label,
  content,
  opened,
  onClose,
  hiddenFrom,
  visibleFrom,
}: {
  label: string;
  content: SidebarContent;
  opened: boolean;
  onClose: () => void;
  hiddenFrom?: 'sm';
  visibleFrom?: 'sm';
}) {
  return (
    <Drawer.Root
      opened={opened}
      onClose={onClose}
      size="var(--ja-sidebar-width)"
      hiddenFrom={hiddenFrom}
      visibleFrom={visibleFrom}
    >
      <Drawer.Overlay />
      <Drawer.Content aria-label={label}>
        <Drawer.Body p={0} className={classes.drawerBody}>
          <Sidebar content={content} onNavigate={onClose} />
        </Drawer.Body>
      </Drawer.Content>
    </Drawer.Root>
  );
}

interface FrameProps {
  /** The area's sidebar, made from the person; none for the member pages. */
  sidebar?: (me: Me) => SidebarContent;
  /** The area's notices, shown above every page in it. */
  notices?: (me: Me) => Schemas['Notice'][];
  /** Narrow, centred column (the member pages) instead of the full width. */
  narrow?: boolean;
  children: ReactNode;
}

export function Frame({ sidebar, notices, narrow = false, children }: FrameProps) {
  const me = useMe();
  const [drawerOpened, drawer] = useDisclosure(false);
  const [folded, fold] = useDisclosure(false);

  if (me.isPending) {
    return (
      <Center h="100vh" role="status" aria-live="polite">
        <Loader size="md" aria-label="Loading" />
      </Center>
    );
  }
  if (me.isError) {
    return (
      <Center h="100vh" p="md">
        <ErrorState error={me.error} onRetry={() => void me.refetch()} />
      </Center>
    );
  }

  const content = sidebar?.(me.data);
  // The admin area's own sidebar is the admin menu: the button folds it.
  const inAdmin = sidebar === adminSidebar;
  const adminMenu = me.data.admin_area && !inAdmin ? adminSidebar(me.data) : undefined;
  const phoneMenu = content ?? adminMenu;
  const drawerButton = (label: string): MenuButton => ({
    opened: drawerOpened,
    label: drawerOpened ? 'Close the menu' : label,
    toggle: drawer.toggle,
  });
  const menu = {
    phone: phoneMenu ? drawerButton('Open the menu') : undefined,
    wide: inAdmin
      ? { opened: false, label: folded ? 'Show the admin menu' : 'Hide the admin menu', toggle: fold.toggle }
      : adminMenu
        ? drawerButton('Open the admin menu')
        : undefined,
  };
  return (
    <AppShell
      header={{ height: 'var(--ja-header-height)' }}
      navbar={
        content
          ? {
              width: 'var(--ja-sidebar-width)',
              breakpoint: 'sm',
              collapsed: { mobile: true, desktop: inAdmin && folded },
            }
          : undefined
      }
      padding={0}
    >
      <AppShell.Header withBorder={false}>
        <TestServerBar />
        <TopBar me={me.data} menu={menu} />
      </AppShell.Header>
      {content ? (
        // Gone on a phone, not only moved out of view: the drawer has it there.
        <AppShell.Navbar withBorder={false} visibleFrom="sm">
          {/* Folded away it is gone, not only out of view: nothing in it to tab to. */}
          {inAdmin && folded ? null : <Sidebar content={content} />}
        </AppShell.Navbar>
      ) : null}
      <AppShell.Main>
        <div className={classes.main} id="content">
          <div className={narrow ? classes.contentNarrow : classes.content}>
            <Notices notices={notices?.(me.data) ?? []} />
            {children}
          </div>
        </div>
      </AppShell.Main>
      {/* Outside <main>, so it is the page's footer; kept clear of the sidebar. */}
      <div className={classes.footerShell}>
        <div className={narrow ? classes.contentNarrow : classes.content}>
          <Footer />
        </div>
      </div>
      {phoneMenu ? (
        <MenuDrawer
          label="Sections"
          content={phoneMenu}
          opened={drawerOpened}
          onClose={drawer.close}
          hiddenFrom="sm"
        />
      ) : null}
      {adminMenu ? (
        // On a wider screen outside the admin area: the admin menu, in from the left.
        <MenuDrawer
          label="Admin menu"
          content={adminMenu}
          opened={drawerOpened}
          onClose={drawer.close}
          visibleFrom="sm"
        />
      ) : null}
    </AppShell>
  );
}
