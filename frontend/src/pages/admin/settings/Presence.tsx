/**
 * On Settings › Updates, beside "Install update now": whether anybody else is
 * using the portal right now, on which pages, and whether somebody is typing
 * -- so an update can wait until they are done (docs/presence.md). Counts and
 * pages, never who. Asked again every 15 seconds while the page is in view.
 * Data: GET /api/v1/admin/presence (aeronautics_members/api/presence.py).
 */
import { Alert, Code, Group, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { api, call, type Schemas } from '../../../api/client';
import { plural } from '../../../lib/format';
import { presenceTab } from '../../../lib/presence';

type Presence = Schemas['PresenceOut'];

export const PRESENCE_EVERY_MS = 15_000;

export const presenceQuery = {
  queryKey: ['admin', 'presence'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/presence', { params: { query: { tab: presenceTab() } } })),
};

/** "just now", "4 minutes ago" -- the quarter of an hour the server keeps. */
export function minutesAgo(iso: string, now = Date.now()): string {
  const minutes = Math.floor((now - new Date(iso).getTime()) / 60_000);
  return minutes < 1 ? 'just now' : `${String(minutes)} ${plural(minutes, 'minute', 'minutes')} ago`;
}

function people(count: number): string {
  return `${String(count)} ${plural(count, 'person', 'people')}`;
}

function Pages({ pages }: { pages: Presence['pages'] }) {
  return (
    <Stack gap={4}>
      {pages.map((page) => (
        <Group key={page.page} gap="xs">
          <Code>{page.page}</Code>
          <Text size="sm" c="dimmed">
            {people(page.people)}
            {page.typing ? `, ${String(page.typing)} typing` : ''}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

export function PresenceNote() {
  const presence = useQuery({ ...presenceQuery, refetchInterval: PRESENCE_EVERY_MS });
  if (!presence.data) return null;
  const { active, signed_in: signedIn, visitors, typing, pages, last_seen_at: lastSeen } = presence.data;
  if (!active) {
    return (
      <Text size="sm" c="dimmed" role="status">
        {lastSeen
          ? `Nobody else is using the portal right now (last seen ${minutesAgo(lastSeen)}).`
          : 'Nobody else is using the portal right now.'}
      </Text>
    );
  }
  return (
    <Stack gap="xs" role="status">
      {typing ? (
        <Alert color="amber" variant="light">
          {`${people(typing)} ${typing === 1 ? 'is' : 'are'} typing. Whatever they send while the site restarts fails; better wait until they are done.`}
        </Alert>
      ) : null}
      <Text size="sm">
        {`${people(active)} using the portal right now: ${String(signedIn)} signed in, ${String(visitors)} ${plural(visitors, 'visitor', 'visitors')}.`}
      </Text>
      <Pages pages={pages} />
    </Stack>
  );
}
