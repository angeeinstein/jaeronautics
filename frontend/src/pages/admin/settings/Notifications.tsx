/**
 * Settings -> Notifications: which notifications go out and from which
 * account, then how each channel stands -- what waits, what is held back by
 * its cooldown, and what failed last. Data: GET|PUT
 * /api/v1/admin/settings/notifications (aeronautics_members/api/admin_settings.py).
 */
import { Checkbox, Select, Stack, Table, Text } from '@mantine/core';
import { useState } from 'react';

import { api, call, type Schemas } from '../../../api/client';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { formatDateTime } from '../../../lib/format';
import { SaveBar, SettingsPage, useSectionSave } from './shared';

type NotificationsOut = Schemas['NotificationsOut'];
type NotificationsIn = Schemas['NotificationsIn'];

const notificationsQuery = {
  queryKey: ['admin', 'settings', 'notifications'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/notifications')),
};

function Health({ data }: { data: NotificationsOut }) {
  return (
    <Panel title="How each channel stands" flush>
      <Table.ScrollContainer minWidth={720} type="native">
        <Table verticalSpacing="sm" aria-label="Notification channels">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Channel</Table.Th>
              <Table.Th scope="col">On</Table.Th>
              <Table.Th scope="col">Waiting</Table.Th>
              <Table.Th scope="col">Held back</Table.Th>
              <Table.Th scope="col">Next sent</Table.Th>
              <Table.Th scope="col">After failures</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.health.map((channel) => (
              <Table.Tr key={channel.channel}>
                <Table.Td>{channel.label}</Table.Td>
                <Table.Td>
                  {channel.enabled ? <Pill tone="active">On</Pill> : <Pill tone="neutral">Off</Pill>}
                </Table.Td>
                <Table.Td className="ja-figures">{channel.pending}</Table.Td>
                <Table.Td>{channel.held_back.length ? channel.held_back.join(', ') : 'Nothing'}</Table.Td>
                <Table.Td>{channel.next_send_at ? formatDateTime(channel.next_send_at) : 'Now'}</Table.Td>
                <Table.Td>
                  {channel.backoff_until ? `Nothing before ${formatDateTime(channel.backoff_until)}` : '–'}
                  {channel.last_failure ? (
                    <Text size="xs" c="dimmed">
                      {channel.last_failure}
                    </Text>
                  ) : null}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Panel>
  );
}

function Form({ data }: { data: NotificationsOut }) {
  const [value, setValue] = useState<NotificationsIn>({
    admin_general: data.admin_general,
    admin_error: data.admin_error,
    user_status: data.user_status,
    sender: data.sender,
  });
  const { save, errors, clear } = useSectionSave(notificationsQuery.queryKey, (body: NotificationsIn) =>
    call(api.PUT('/api/v1/admin/settings/notifications', { body })),
  );
  const toggle = (key: 'admin_general' | 'admin_error' | 'user_status') => (checked: boolean) => {
    setValue({ ...value, [key]: checked });
  };

  return (
    <Stack gap="lg">
      <Panel title="What goes out">
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            Admins get these once their email address is verified. What waits for review and application
            errors are sent as digests, each with its own cooldown; emails to members go out at once.
          </Text>
          <Checkbox
            label="What waits for review, to the admins"
            checked={value.admin_general}
            onChange={(event) => {
              toggle('admin_general')(event.currentTarget.checked);
            }}
          />
          <Checkbox
            label="Application errors, to the admins"
            checked={value.admin_error}
            onChange={(event) => {
              toggle('admin_error')(event.currentTarget.checked);
            }}
          />
          <Checkbox
            label="Emails to members about their membership"
            checked={value.user_status}
            onChange={(event) => {
              toggle('user_status')(event.currentTarget.checked);
            }}
          />
          <Select
            label="From"
            description="Empty: the welcome email's sender."
            placeholder="The welcome email's sender"
            data={data.senders}
            clearable
            value={value.sender ?? null}
            error={errors.sender}
            onChange={(sender) => {
              setValue({ ...value, sender });
              clear('sender');
            }}
            maw={420}
          />
        </Stack>
      </Panel>
      <SaveBar
        busy={save.isPending}
        onSave={() => {
          save.mutate(value);
        }}
      />
      <Health data={data} />
    </Stack>
  );
}

export function Notifications() {
  return (
    <SettingsPage
      title="Notifications"
      description="Which notifications go out, from which account, and how each channel stands."
      query={notificationsQuery}
    >
      {(data) => <Form data={data} />}
    </SettingsPage>
  );
}
