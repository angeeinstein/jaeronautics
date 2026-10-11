/**
 * Admin › Money › Stripe's fees: who carries them -- the association, or the
 * teams: then Stripe's fee comes off each team fee, and each credit sale
 * carries its share of the fee of the top-up that paid for it. It counts for
 * all money at once, past and future (docs/money-ledger-plan.md). Apart from
 * that, a share the association keeps of what teams sell for credit, kept
 * with each sale from then on. Data: GET|PUT /api/v1/admin/money/settings.
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
            ? "Each team fee counts less Stripe's fee for it, and each credit sale less its share of the fee of the top-up that paid for it. For all money, also what was paid before."
            : 'Teams get what their members paid and what they sold, less refunds.'}
        </Text>
      </Stack>
      <Group align="flex-end" gap="md">
        <NumberInput
          label="Kept of what teams sell for credit"
          description="A share for the association, on top of any fee. For sales from now on."
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
      <SaveBar
        busy={save.isPending}
        onSave={() => {
          save.mutate({
            fee_payer: payer,
            credit_share_bps: Math.round(Number(share || 0) * 100),
            difference_emails: data.difference_emails,
          });
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
