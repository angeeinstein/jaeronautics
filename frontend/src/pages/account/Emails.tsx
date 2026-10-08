/**
 * Whether an email address is confirmed -- each is on its own -- and the way
 * to have its link sent again: under each address in Contact details, and in
 * Email addresses for an account without a membership, which has no contact
 * details. For a returning student the university address is what gives
 * their old forum account back.
 */
import { Button, Group, Stack, Text } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';

import { api, call, type Schemas } from '../../api/client';
import { Panel } from '../../components/Panel';
import { Pill } from '../../components/Pill';
import { notifyFailed, notifyMessage } from '../../lib/notify';

type Address = Schemas['EmailAddressOut'];

/** An address's confirmation link, sent again. */
export function ResendButton({
  which,
  children = 'Send the link again',
  size = 'compact-xs',
}: {
  which: 'private' | 'work';
  children?: string;
  size?: 'compact-xs' | 'xs';
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
    <Button
      size={size}
      variant="default"
      loading={resend.isPending}
      onClick={() => {
        resend.mutate();
      }}
    >
      {children}
    </Button>
  );
}

/** Confirmed or not, and for one that is not, its link sent again. */
export function AddressState({ address, which }: { address: Address; which: 'private' | 'work' }) {
  return (
    <Group gap="xs">
      <Pill tone={address.confirmed ? 'active' : 'pending'}>
        {address.confirmed ? 'Confirmed' : 'Not confirmed'}
      </Pill>
      {address.confirmed ? null : <ResendButton which={which} />}
    </Group>
  );
}

function AddressLine({ label, address }: { label: string; address: Address }) {
  return (
    <Stack gap={4}>
      <div>
        <Text size="xs" c="dimmed">
          {label}
        </Text>
        <Text style={{ overflowWrap: 'anywhere' }}>{address.address}</Text>
      </div>
      <AddressState address={address} which="private" />
    </Stack>
  );
}

/** For an account without a membership: its login address, which there are no contact details to show. */
export function EmailsCard({ email }: { email: Address }) {
  return (
    <Panel title="Email address">
      <AddressLine label="Your login" address={email} />
    </Panel>
  );
}
