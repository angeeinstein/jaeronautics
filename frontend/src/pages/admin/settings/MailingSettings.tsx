/**
 * Settings › Mailings (docs/messages-plan.md): which mail account
 * announcements, team mailings and the contact form's emails go out from --
 * the notifications' one, or another provider -- and how many may go out an
 * hour and a day, under the provider's limits (Brevo's free plan: 300 a day).
 * Data: GET|PUT /api/v1/admin/settings/mailings.
 */
import { NumberInput, Select, SimpleGrid, Stack, Text } from '@mantine/core';
import { useState } from 'react';

import { api, call, type Schemas } from '../../../api/client';
import { Panel } from '../../../components/Panel';
import { SaveBar, SettingsPage, useSectionSave } from './shared';

type MailingSettingsOut = Schemas['MailingSettingsOut'];

const mailingSettingsQuery = {
  queryKey: ['admin', 'settings', 'mailings'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/mailings')),
};

const BELOW = ['label', 'input', 'description', 'error'] as ('label' | 'input' | 'description' | 'error')[];

function Form({ data }: { data: MailingSettingsOut }) {
  const [sender, setSender] = useState<string | null>(data.sender || null);
  const [perHour, setPerHour] = useState<number | string>(data.per_hour);
  const [perDay, setPerDay] = useState<number | string>(data.per_day);
  const { save, errors, clear } = useSectionSave(
    mailingSettingsQuery.queryKey,
    (body: Schemas['MailingSettingsIn']) => call(api.PUT('/api/v1/admin/settings/mailings', { body })),
  );
  return (
    <Stack gap="lg">
      <Panel title="Sending">
        <Stack gap="md">
          <Select
            label="Mail account"
            description={`Empty: the notifications' account${data.default_sender ? ` (${data.default_sender})` : ''}.`}
            inputWrapperOrder={BELOW}
            placeholder="The notifications' account"
            data={data.accounts}
            clearable
            value={sender}
            error={errors.sender}
            onChange={(value) => {
              setSender(value);
              clear('sender');
            }}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <NumberInput
              label="At most an hour"
              inputWrapperOrder={BELOW}
              min={1}
              allowDecimal={false}
              allowNegative={false}
              value={perHour}
              error={errors.per_hour}
              onChange={(value) => {
                setPerHour(value);
                clear('per_hour');
              }}
            />
            <NumberInput
              label="At most a day"
              description="Brevo's free plan sends 300 a day."
              inputWrapperOrder={BELOW}
              min={1}
              allowDecimal={false}
              allowNegative={false}
              value={perDay}
              error={errors.per_day}
              onChange={(value) => {
                setPerDay(value);
                clear('per_day');
              }}
            />
          </SimpleGrid>
          <Text size="sm" c="dimmed">
            Announcements and team mailings go out a few at a time, every two minutes, within these limits.
            The contact form&apos;s messages go out from the same account, at once.
          </Text>
        </Stack>
      </Panel>
      <SaveBar
        busy={save.isPending}
        onSave={() => {
          save.mutate({ sender: sender ?? '', per_hour: Number(perHour), per_day: Number(perDay) });
        }}
      />
    </Stack>
  );
}

export function MailingSettings() {
  return (
    <SettingsPage
      title="Mailings"
      description="Where announcements and team mailings are sent from, and how many at a time."
      query={mailingSettingsQuery}
    >
      {(data) => <Form data={data} />}
    </SettingsPage>
  );
}
