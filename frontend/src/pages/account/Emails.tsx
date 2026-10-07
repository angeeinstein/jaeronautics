/**
 * My Account › Email addresses: the private one, which is the login, and the
 * university or company one -- each confirmed on its own, so each shows
 * whether it is and can have its link sent again. For a returning student the
 * university address is what gives their old forum account back.
 */
import { Button, Group, Stack, Text } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';

import { api, call, type Schemas } from '../../api/client';
import { Panel } from '../../components/Panel';
import { Pill } from '../../components/Pill';
import { notifyFailed, notifyMessage } from '../../lib/notify';

type Address = Schemas['EmailAddressOut'];

function AddressLine({
  label,
  address,
  which,
}: {
  label: string;
  address: Address;
  which: 'private' | 'work';
}) {
  const resend = useMutation({
    mutationFn: () =>
      call(
        which === 'private'
          ? api.POST('/api/v1/account/emails/private/confirmation')
          : api.POST('/api/v1/account/emails/work/confirmation'),
      ),
    onSuccess: notifyMessage,
    onError: notifyFailed,
  });
  return (
    <Stack gap={4}>
      <Group justify="space-between" gap="xs" wrap="nowrap" align="flex-start">
        <div>
          <Text size="xs" c="dimmed">
            {label}
          </Text>
          <Text style={{ overflowWrap: 'anywhere' }}>{address.address}</Text>
        </div>
        <Pill tone={address.confirmed ? 'active' : 'pending'}>
          {address.confirmed ? 'Confirmed' : 'Not confirmed'}
        </Pill>
      </Group>
      {address.confirmed ? null : (
        <Group>
          <Button
            size="xs"
            variant="default"
            loading={resend.isPending}
            onClick={() => {
              resend.mutate();
            }}
          >
            Send the link again
          </Button>
        </Group>
      )}
    </Stack>
  );
}

export function EmailsCard({ email, workEmail }: { email: Address; workEmail: Address | null }) {
  return (
    <Panel title="Email addresses">
      <Stack gap="md">
        <AddressLine label="Private (your login)" address={email} which="private" />
        {workEmail ? <AddressLine label="University or company" address={workEmail} which="work" /> : null}
      </Stack>
    </Panel>
  );
}
