/**
 * My Account › Forum: where things stand, the forum username, the profile
 * picture the forum needs -- and the way in, once it is open.
 */
import { Alert, Button, Group, Image, Stack, Text } from '@mantine/core';

import type { Schemas } from '../../api/client';
import { Details } from '../../components/Details';
import { Panel } from '../../components/Panel';

type Forum = Schemas['ForumCardOut'];

export function forumUsername(forum: Forum): string | null {
  if (forum.reconnect_waiting) return 'Your old one, once reconnected';
  return forum.username;
}

export function ForumCard({ forum }: { forum: Forum }) {
  const picture = forum.picture;
  return (
    <Panel
      title="Forum"
      actions={
        forum.may_open ? (
          <Button component="a" href={forum.open_url} size="xs">
            Open forum
          </Button>
        ) : null
      }
    >
      <Stack gap="md">
        <Text size="sm">{forum.message}</Text>
        <Details compact items={[['Username', forumUsername(forum)]]} />
        {forum.problem ? (
          <Alert color="amber" variant="light" role="status">
            <Text size="sm">The forum could not be updated just now. We will try again by ourselves.</Text>
          </Alert>
        ) : null}
        {picture ? (
          <Stack gap="sm">
            {picture.rejected_reason ? (
              <Alert color="gray" variant="light">
                <Text size="sm">{`Reason: ${picture.rejected_reason}`}</Text>
              </Alert>
            ) : null}
            {picture.pending_url ? (
              <Image src={picture.pending_url} alt="Your picture, waiting for review" w={120} h={120} />
            ) : null}
            {picture.upload ? (
              <Group>
                <Button component="a" href="/forum" variant={picture.pending_url ? 'default' : 'filled'}>
                  {picture.pending_url ? 'Upload another picture' : 'Upload a picture'}
                </Button>
              </Group>
            ) : null}
          </Stack>
        ) : null}
      </Stack>
    </Panel>
  );
}
