/**
 * Admin › Money › Stripe's fees: who carries them. On a team fee, the
 * association (as before) or the team -- then Stripe's actual fee for each
 * payment is taken off what the team is owed. For credit there is no one fee
 * per sale, so the association may keep a share of what teams sell instead.
 * Either counts from when it is set: what was paid and sold before stays as
 * it was counted. Data: GET|PUT /api/v1/admin/money/settings.
 */
import { Group, NumberInput, SegmentedControl, Stack, Text } from '@mantine/core';
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
  const [share, setShare] = useState<number | string>(data.credit_share_bps / 100);
  const { save, errors, clear } = useSectionSave(['admin', 'money'], (body: MoneySettings) =>
    call(api.PUT('/api/v1/admin/money/settings', { body })),
  );
  return (
    <Stack gap="md">
      <Stack gap={6}>
        <Text fw={600}>On team fees</Text>
        <SegmentedControl
          aria-label="Who pays Stripe's fee on team fees"
          value={payer}
          onChange={(value) => {
            setPayer(value);
          }}
          data={[
            { label: 'The association pays', value: 'association' },
            { label: 'Taken from the team', value: 'team' },
          ]}
          w="fit-content"
        />
        <Text size="sm" c="dimmed">
          {payer === 'team'
            ? "Each team fee paid from now on counts for the team less Stripe's actual fee for it, known a day later."
            : 'Teams get exactly what their members paid, less refunds.'}
        </Text>
      </Stack>
      <Group align="flex-end" gap="md">
        <NumberInput
          label="Kept of what teams sell for credit"
          description="Covers Stripe's fee on top-ups, which is spread over everything a top-up buys."
          inputWrapperOrder={['label', 'input', 'description', 'error']}
          suffix=" %"
          decimalScale={2}
          min={0}
          max={20}
          allowNegative={false}
          value={share}
          error={errors.credit_share_bps}
          onChange={(value) => {
            setShare(value);
            clear('credit_share_bps');
          }}
          maw={360}
        />
      </Group>
      <Text size="sm" c="dimmed">
        Changes count from now on: what was paid and sold before stays as it was counted.
      </Text>
      <SaveBar
        busy={save.isPending}
        onSave={() => {
          save.mutate({ fee_payer: payer, credit_share_bps: Math.round(Number(share || 0) * 100) });
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
