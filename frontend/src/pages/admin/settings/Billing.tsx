/**
 * Settings -> Membership fee: the Stripe keys and the membership's price. A
 * new price is checked with Stripe and every running membership moves to it
 * from its next renewal, each member emailed two weeks before -- so a changed
 * price asks again before it is saved. Of the secrets only whether one is set
 * is ever shown. Data: GET|PUT /api/v1/admin/settings/billing.
 */
import { Alert, Group, PasswordInput, SimpleGrid, Stack, Text, TextInput } from '@mantine/core';
import { useState } from 'react';

import { api, call, type Schemas } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Panel } from '../../../components/Panel';
import { notifyNote } from '../../../lib/notify';
import { SaveBar, secretHint, SettingsPage, useSectionSave } from './shared';

type BillingOut = Schemas['BillingOut'];
type BillingIn = Schemas['BillingIn'];

const billingQuery = {
  queryKey: ['admin', 'settings', 'billing'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/billing')),
};

function Form({ data }: { data: BillingOut }) {
  const [value, setValue] = useState<BillingIn>({
    publishable_key: data.publishable_key,
    price_id: data.price_id,
    secret_key: null,
    webhook_secret: null,
  });
  const { save, errors, clear } = useSectionSave(
    billingQuery.queryKey,
    (body: BillingIn) => call(api.PUT('/api/v1/admin/settings/billing', { body })),
    (out) => {
      if (out.moving)
        notifyNote(
          `${String(out.moving)} running membership(s) move to the new price from their next renewal. Each member is emailed two weeks before.`,
        );
    },
  );
  const set = <K extends keyof BillingIn>(key: K, next: BillingIn[K]) => {
    setValue({ ...value, [key]: next });
    clear(key);
  };
  const newPrice = (value.price_id ?? '').trim() !== (data.price_id ?? '') && Boolean(value.price_id?.trim());
  const submit = () => {
    save.mutate(value);
  };

  return (
    <Stack gap="lg">
      <Panel title="Stripe">
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            The keys for paying online, the customer portal and checking Stripe&apos;s messages to the portal.
            They stay on this server and are in no export.
          </Text>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label="Publishable key"
              placeholder="pk_live_…"
              value={value.publishable_key ?? ''}
              error={errors.publishable_key}
              onChange={(event) => {
                set('publishable_key', event.currentTarget.value || null);
              }}
            />
            <TextInput
              label="Membership price"
              description="The Stripe price ID of the membership fee."
              placeholder="price_…"
              value={value.price_id ?? ''}
              error={errors.price_id}
              onChange={(event) => {
                set('price_id', event.currentTarget.value || null);
              }}
            />
            <PasswordInput
              label="Secret key"
              description={secretHint(data.secret_key_set)}
              autoComplete="off"
              value={value.secret_key ?? ''}
              onChange={(event) => {
                set('secret_key', event.currentTarget.value || null);
              }}
            />
            <PasswordInput
              label="Webhook secret"
              description={secretHint(data.webhook_secret_set)}
              autoComplete="off"
              value={value.webhook_secret ?? ''}
              onChange={(event) => {
                set('webhook_secret', event.currentTarget.value || null);
              }}
            />
          </SimpleGrid>
          {newPrice ? (
            <Alert color="amber" variant="light">
              A new price: it is checked with Stripe, and every running membership moves to it from its next
              renewal. Every member is emailed two weeks before.
            </Alert>
          ) : null}
        </Stack>
      </Panel>
      {newPrice ? (
        <Group justify="flex-end">
          <ConfirmButton
            color="brand"
            confirmLabel="Yes, change the price"
            loading={save.isPending}
            onConfirm={submit}
          >
            Save
          </ConfirmButton>
        </Group>
      ) : (
        <SaveBar busy={save.isPending} onSave={submit} />
      )}
    </Stack>
  );
}

export function Billing() {
  return (
    <SettingsPage
      title="Membership fee"
      description="The Stripe keys and the membership's price."
      query={billingQuery}
    >
      {(data) => <Form data={data} />}
    </SettingsPage>
  );
}
