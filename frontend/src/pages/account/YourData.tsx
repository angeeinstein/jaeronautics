/**
 * My Account › Your data: everything held about this person as a file, and
 * deleting the account -- by a link sent to the login address, so a borrowed
 * laptop cannot do it. Leaving and deleting are two decisions: while a paid
 * membership runs, the card says what deleting would cost and offers
 * cancelling instead.
 */
import { Button, Group, Stack, Text, Title } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';

import { api, call, type Schemas } from '../../api/client';
import { Panel } from '../../components/Panel';
import { notifyFailed, notifyMessage } from '../../lib/notify';
import { useBilling } from './Membership';
import { Note } from './shared';

export function YourDataCard({
  exportUrl,
  deletionNote,
  mayCancelInstead,
}: {
  exportUrl: string;
  deletionNote: Schemas['NoteOut'] | null;
  mayCancelInstead: boolean;
}) {
  const billing = useBilling();
  const request = useMutation({
    mutationFn: () => call(api.POST('/api/v1/account/deletion')),
    onSuccess: notifyMessage,
    onError: notifyFailed,
  });
  return (
    <Panel title="Your data">
      <Stack gap="lg">
        <Stack gap="sm">
          <Text size="sm">You can download everything we store about you.</Text>
          <Group>
            {/* A download from Flask, which logs it. */}
            <Button component="a" href={exportUrl} variant="default">
              Download my data (JSON)
            </Button>
          </Group>
        </Stack>
        <Stack gap="sm">
          <Title order={3} size="h5">
            Delete my account
          </Title>
          <Text size="sm">
            We erase your name, address and contact details, and your login stops working. This cannot be
            undone. We keep a record of the membership fees you paid, without your personal details, because
            bookkeeping law requires it.
          </Text>
          {deletionNote ? <Note note={deletionNote} /> : null}
          <Group>
            {mayCancelInstead ? (
              <Button
                loading={billing.isPending}
                onClick={() => {
                  billing.mutate();
                }}
              >
                Cancel my membership instead
              </Button>
            ) : null}
            <Button
              color="red"
              variant="outline"
              loading={request.isPending}
              onClick={() => {
                request.mutate();
              }}
            >
              Email me a deletion link
            </Button>
          </Group>
          <Text size="xs" c="dimmed">
            Nothing is deleted until you open the link.
          </Text>
        </Stack>
      </Stack>
    </Panel>
  );
}
