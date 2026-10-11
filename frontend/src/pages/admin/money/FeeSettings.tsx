/**
 * Admin › Money › Stripe's fees: who carries them -- the association, or the
 * teams: then Stripe's fee comes off each team fee, and each credit sale
 * carries its share of the fee of the top-up that paid for it. It counts for
 * all money at once, past and future (docs/money-ledger-plan.md).
 * Data: GET|PUT /api/v1/admin/money/settings.
 */
import { SegmentedControl, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call, type Schemas } from '../../../api/client';
import { Panel } from '../../../components/Panel';
import { SaveBar, useSectionSave } from '../settings/shared';

type MoneySettings = Schemas['MoneySettings'];

const settingsQuery = {
  queryKey: ['admin', 'money', 'settings'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/money/settings')),
};

function Form({ data }: { data: MoneySettings }) {
  const [payer, setPayer] = useState(data.fee_payer);
  const { save } = useSectionSave(['admin', 'money'], (body: MoneySettings) =>
    call(api.PUT('/api/v1/admin/money/settings', { body })),
  );
  return (
    <Stack gap="md">
      <Stack gap={6}>
        <Text fw={600}>Who carries them</Text>
        <SegmentedControl
          aria-label="Who carries Stripe's fees"
          value={payer}
          onChange={(value) => {
            setPayer(value);
          }}
          data={[
            { label: 'The association', value: 'association' },
            { label: 'The teams', value: 'team' },
          ]}
          w="fit-content"
        />
        <Text size="sm" c="dimmed">
          {payer === 'team'
            ? "Stripe's fee comes off each team fee, and off each credit sale its share of the top-up's fee."
            : 'Teams get what their members paid and what they sold, less refunds.'}
        </Text>
      </Stack>
      <SaveBar
        busy={save.isPending}
        onSave={() => {
          save.mutate({ fee_payer: payer, difference_emails: data.difference_emails });
        }}
      />
    </Stack>
  );
}

export function FeeSettings() {
  const settings = useQuery(settingsQuery);
  return (
    <Panel title="Stripe's fees">
      {settings.data ? (
        <Form key={JSON.stringify(settings.data)} data={settings.data} />
      ) : (
        <Text size="sm" c="dimmed">
          …
        </Text>
      )}
    </Panel>
  );
}
