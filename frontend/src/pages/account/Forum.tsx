/**
 * The forum's own page (/forum, the link in the welcome email): what is still
 * to be done before the forum opens -- usually the profile picture. Once the
 * forum is open, Flask sends the browser straight in instead of here.
 * Data: GET /api/v1/account/forum.
 */
import { Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { api, call } from '../../api/client';
import { PageHeader } from '../../components/PageHeader';
import { ErrorState, LoadingState } from '../../components/States';
import { ForumCard } from './ForumCard';
import { accountKey } from './shared';

export function Forum() {
  const forum = useQuery({
    queryKey: [...accountKey, 'forum'] as const,
    queryFn: () => call(api.GET('/api/v1/account/forum')),
  });
  return (
    <>
      <PageHeader
        title="Forum"
        description="What is still to be done before member content opens up in the forum."
        crumbs={[{ label: 'My Account', to: '/account' }, { label: 'Forum' }]}
      />
      {forum.isPending ? (
        <LoadingState />
      ) : forum.isError ? (
        <ErrorState error={forum.error} onRetry={() => void forum.refetch()} />
      ) : (
        <Stack gap="lg">
          <ForumCard forum={forum.data} />
          {forum.data.may_open ? (
            <Text size="sm" c="dimmed">
              If you were not taken to the forum by yourself, use Open forum above.
            </Text>
          ) : null}
        </Stack>
      )}
    </>
  );
}
