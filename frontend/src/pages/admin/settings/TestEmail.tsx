/**
 * Settings -> Test email: one email from a template, filled in as a member's
 * would be, from one of the mail accounts -- to see a template, or that an
 * account sends. Data: GET|POST /api/v1/admin/settings/test-email
 * (aeronautics_members/api/admin_mail.py).
 */
import { Alert, Button, Group, Select, Stack, TextInput } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { api, ApiError, call, type Schemas } from '../../../api/client';
import { useMe } from '../../../api/session';
import { Panel } from '../../../components/Panel';
import { LoadingState } from '../../../components/States';
import { notifyDone, notifyFailed } from '../../../lib/notify';
import { SettingsPage } from './shared';

type TestEmailIn = Schemas['TestEmailIn'];

const testEmailQuery = {
  queryKey: ['admin', 'settings', 'test-email'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/test-email')),
};

function Form({ data, email }: { data: Schemas['TestEmailOut']; email: string }) {
  const [value, setValue] = useState<TestEmailIn>({
    sender: data.senders[0] ?? '',
    recipient: email,
    template: data.templates[0] ?? '',
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const send = useMutation({
    mutationFn: (body: TestEmailIn) => call(api.POST('/api/v1/admin/settings/test-email', { body })),
    onSuccess: (result) => {
      if (result.ok) notifyDone(`Sent to ${value.recipient}.`);
      else
        notifyFailed(
          new ApiError(200, 'send_failed', 'The email could not be sent. The server log says why.'),
        );
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const set = (key: keyof TestEmailIn, next: string) => {
    setValue({ ...value, [key]: next });
    setErrors({});
  };

  if (!data.senders.length) {
    return (
      <Alert color="amber" variant="light">
        There is no mail account to send from yet. Add one under Mail accounts.
      </Alert>
    );
  }
  return (
    <Panel title="Send a test email">
      <Stack gap="md" maw={520}>
        <Select
          label="From"
          data={data.senders}
          allowDeselect={false}
          value={value.sender}
          error={errors.sender}
          onChange={(sender) => {
            set('sender', sender ?? '');
          }}
        />
        <TextInput
          type="email"
          label="To"
          value={value.recipient}
          error={errors.recipient}
          onChange={(event) => {
            set('recipient', event.currentTarget.value);
          }}
        />
        <Select
          label="Template"
          description="Filled in with sample values, as a member's would be."
          data={data.templates}
          allowDeselect={false}
          searchable
          value={value.template}
          error={errors.template}
          onChange={(template) => {
            set('template', template ?? '');
          }}
        />
        <Group justify="flex-end">
          <Button
            loading={send.isPending}
            disabled={!value.recipient.trim()}
            onClick={() => {
              send.mutate(value);
            }}
          >
            Send
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

export function TestEmail() {
  // To the person sending it, so the form waits for who that is.
  const me = useMe();
  return (
    <SettingsPage
      title="Test email"
      description="One email from a template, as a member would get it."
      query={testEmailQuery}
    >
      {(data) => (me.data ? <Form data={data} email={me.data.email} /> : <LoadingState />)}
    </SettingsPage>
  );
}
