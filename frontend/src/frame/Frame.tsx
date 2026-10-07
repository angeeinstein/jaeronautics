/**
 * The page around every signed-in page: the top bar, the area's sidebar
 * where it has one (a drawer on a phone), the area's notices, the page, and
 * the footer.
 * Waits for the person (``/me``) once; a page inside never shows without it.
 */
import { Alert, AppShell, Button, Center, Drawer, Group, Loader, Stack, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import type { ReactNode } from 'react';

import { AppLink } from '../app/AppLink';
import type { Schemas } from '../api/client';
import { type Me, useMe } from '../api/session';
import { ErrorState } from '../components/States';
import { Footer } from './Footer';
import classes from './Frame.module.css';
import type { SidebarContent } from './navigation';
import { Sidebar } from './Sidebar';
import { TestServerBar } from './TestServerBar';
import { TopBar } from './TopBar';

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
  return (
    <AppShell
      header={{ height: 'var(--ja-header-height)' }}
      navbar={
        content
          ? { width: 'var(--ja-sidebar-width)', breakpoint: 'sm', collapsed: { mobile: true } }
          : undefined
      }
      padding={0}
    >
      <AppShell.Header withBorder={false}>
        <TestServerBar />
        <TopBar me={me.data} menu={content ? { opened: drawerOpened, toggle: drawer.toggle } : undefined} />
      </AppShell.Header>
      {content ? (
        // Gone on a phone, not only moved out of view: the drawer has it there.
        <AppShell.Navbar withBorder={false} visibleFrom="sm">
          <Sidebar content={content} />
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
      {content ? (
        <Drawer
          opened={drawerOpened}
          onClose={drawer.close}
          size="16.25rem"
          padding={0}
          withCloseButton={false}
          hiddenFrom="sm"
          aria-label="Sections"
        >
          {/* A page chosen in the drawer replaces it. */}
          <Sidebar content={content} onNavigate={drawer.close} />
        </Drawer>
      ) : null}
    </AppShell>
  );
}
