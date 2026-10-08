/**
 * My Account › Forum and picture (/account/forum): one's forum account, the
 * profile picture it shows -- the one waiting for review, a new one -- and the
 * way in. Also the forum's own address (/forum, the link in the welcome
 * email) while something is still to be done before the forum opens; once it
 * is open, Flask sends the browser straight in instead of here.
 * Data: GET /api/v1/account/forum.
 */
import { Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useLocation } from 'react-router';

import { api, call } from '../../api/client';
import { PageHeader } from '../../components/PageHeader';
import { ErrorState, LoadingState } from '../../components/States';
import { ForumCard } from './ForumCard';
import { accountKey } from './shared';

export function Forum() {
  // Sent here by /forum: the forum would have opened by itself, were it ready.
  const viaForum = useLocation().pathname === '/forum';
  const forum = useQuery({
    queryKey: [...accountKey, 'forum'] as const,
    queryFn: () => call(api.GET('/api/v1/account/forum')),
  });
  return (
    <>
      <PageHeader
        title="Forum and picture"
        description="Your forum account, and the picture it shows of you."
        crumbs={[{ label: 'My Account', to: '/account' }, { label: 'Forum and picture' }]}
      />
      {forum.isPending ? (
        <LoadingState />
      ) : forum.isError ? (
        <ErrorState error={forum.error} onRetry={() => void forum.refetch()} />
      ) : (
        <Stack gap="lg">
          <ForumCard forum={forum.data} />
          {viaForum && forum.data.may_open ? (
            <Text size="sm" c="dimmed">
              If you were not taken to the forum by yourself, use Open forum above.
            </Text>
          ) : null}
        </Stack>
      )}
    </>
  );
}
