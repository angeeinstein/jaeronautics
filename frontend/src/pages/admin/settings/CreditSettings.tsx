/**
 * Settings › Credit: switching credit on or off, the Stripe product top-ups
 * are sold as, and the limits -- the smallest top-up, the most one person
 * may hold, the amounts offered as buttons (docs/credit-plan.md). Amounts
 * are typed in euros and sent in cents. Data: GET|PUT
 * /api/v1/admin/settings/credit.
 */
import { Alert, Checkbox, NumberInput, SimpleGrid, Stack, Text, TextInput } from '@mantine/core';
import { useState } from 'react';

import { api, call, type Schemas } from '../../../api/client';
import { Panel } from '../../../components/Panel';
import { SaveBar, SettingsPage, useSectionSave } from './shared';

type CreditSettingsOut = Schemas['CreditSettingsOut'];
type CreditSettingsIn = Schemas['CreditSettingsIn'];

const creditSettingsQuery = {
  queryKey: ['admin', 'settings', 'credit'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/credit')),
};

const BELOW = ['label', 'input', 'description', 'error'] as ('label' | 'input' | 'description' | 'error')[];

/** "10, 15, 20" as cents; null where something is not an amount. */
export function parseAmounts(text: string): number[] | null {
  const parts = text
    .split(/[,;\s]+/)
    .map((part) => part.replace('€', '').replace(',', '.').trim())
    .filter(Boolean);
  const cents = parts.map((part) => Math.round(Number(part) * 100));
  return cents.every((value) => Number.isFinite(value) && value > 0) ? cents : null;
}

function euros(cents: number): string {
  return cents % 100 === 0 ? String(cents / 100) : (cents / 100).toFixed(2);
}

function Form({ data }: { data: CreditSettingsOut }) {
  const [enabled, setEnabled] = useState(data.enabled);
  const [productId, setProductId] = useState(data.product_id ?? '');
  const [least, setLeast] = useState<number | string>(data.min_top_up_cents / 100);
  const [most, setMost] = useState<number | string>(data.max_balance_cents / 100);
  const [suggested, setSuggested] = useState(data.suggested_cents.map(euros).join(', '));
  const [eps, setEps] = useState(data.eps);
  const { save, errors, clear } = useSectionSave(creditSettingsQuery.queryKey, (body: CreditSettingsIn) =>
    call(api.PUT('/api/v1/admin/settings/credit', { body })),
  );
  const amounts = parseAmounts(suggested);
  const submit = () => {
    if (amounts === null) return;
    save.mutate({
      enabled,
      product_id: productId.trim() || null,
      min_top_up_cents: Math.round(Number(least) * 100),
      max_balance_cents: Math.round(Number(most) * 100),
      suggested_cents: amounts,
      eps,
    });
  };

  return (
    <Stack gap="lg">
      {!data.stripe_ready ? (
        <Alert color="amber" variant="light">
          Stripe&apos;s keys are not set yet (Settings › Membership fee). Nothing can be paid until they are.
        </Alert>
      ) : null}
      <Panel title="Credit">
        <Stack gap="md">
          <Checkbox
            label="Credit is on"
            description="Members see their credit in My Account and can top it up. Off, nobody sees it; balances and history are kept."
            checked={enabled}
            onChange={(event) => {
              setEnabled(event.currentTarget.checked);
              clear('product_id');
            }}
          />
          <TextInput
            label="Stripe product"
            description="In Stripe: Product catalogue › Add product, named Credit, with any price (the portal sets the amount). Paste its ID."
            inputWrapperOrder={BELOW}
            placeholder="prod_…"
            value={productId}
            error={errors.product_id}
            onChange={(event) => {
              setProductId(event.currentTarget.value);
              clear('product_id');
            }}
          />
          <Checkbox
            label="EPS beside cards"
            description="Austrian online banking. Switch it on in Stripe first (Settings › Payment methods). Cards include Apple Pay and Google Pay."
            checked={eps}
            onChange={(event) => {
              setEps(event.currentTarget.checked);
            }}
          />
        </Stack>
      </Panel>
      <Panel title="Limits">
        <Stack gap="md">
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
            <NumberInput
              label="Smallest top-up"
              description="Stripe's fee is most of a small one."
              inputWrapperOrder={BELOW}
              prefix="€"
              decimalScale={2}
              min={1}
              allowNegative={false}
              value={least}
              error={errors.min_top_up_cents}
              onChange={(value) => {
                setLeast(value);
                clear('min_top_up_cents');
              }}
            />
            <NumberInput
              label="Most on one account"
              description="A top-up never goes past it."
              inputWrapperOrder={BELOW}
              prefix="€"
              decimalScale={2}
              min={1}
              allowNegative={false}
              value={most}
              error={errors.max_balance_cents}
              onChange={(value) => {
                setMost(value);
                clear('max_balance_cents');
              }}
            />
            <TextInput
              label="Suggested amounts"
              description="In euros, as buttons. Up to four."
              inputWrapperOrder={BELOW}
              placeholder="10, 15, 20"
              value={suggested}
              error={amounts === null ? 'Amounts in euros, separated by commas.' : errors.suggested_cents}
              onChange={(event) => {
                setSuggested(event.currentTarget.value);
                clear('suggested_cents');
              }}
            />
          </SimpleGrid>
          <Text size="sm" c="dimmed">
            Topping up is for active members. Refunds go back to the card or account a top-up was paid with.
          </Text>
        </Stack>
      </Panel>
      <SaveBar busy={save.isPending} onSave={submit} />
    </Stack>
  );
}

export function CreditSettings() {
  return (
    <SettingsPage
      title="Credit"
      description="A balance members top up, for coffee, drinks and more."
      query={creditSettingsQuery}
    >
      {(data) => <Form data={data} />}
    </SettingsPage>
  );
}
