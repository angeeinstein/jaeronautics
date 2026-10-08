/**
 * The admins' part of a team: its name and forum group, and its fee -- the
 * same fields for a new team and an existing one. How people join, how many
 * it takes, its page and its access list are its leads' (the team's own
 * settings); what needs Stripe or the forum's admin side is the admins'.
 */
import { Select, SimpleGrid, TextInput } from '@mantine/core';

import type { Schemas } from '../../../api/client';
import { PAYMENT_CHOICES } from './shared';

export type Details = Schemas['TeamDetailsIn'];
export type FeeFields = Schemas['FeeIn'];

/** The help text under the box, so boxes side by side line up whatever their help says. */
const BELOW: ('label' | 'input' | 'description' | 'error')[] = ['label', 'input', 'description', 'error'];

export function DetailsFields({
  value,
  onChange,
  errors = {},
}: {
  value: Details;
  onChange: (value: Details) => void;
  errors?: Record<string, string>;
}) {
  return (
    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
      <TextInput
        label="Name"
        required
        maxLength={120}
        inputWrapperOrder={BELOW}
        value={value.name}
        error={errors.name}
        onChange={(event) => {
          onChange({ ...value, name: event.currentTarget.value });
        }}
      />
      <TextInput
        label="Forum group"
        description="Optional. Its active members are put in this forum group, which has to exist on the forum."
        maxLength={100}
        inputWrapperOrder={BELOW}
        value={value.forum_group ?? ''}
        error={errors.forum_group}
        onChange={(event) => {
          onChange({ ...value, forum_group: event.currentTarget.value || null });
        }}
      />
    </SimpleGrid>
  );
}

export function FeeFieldsEditor({
  value,
  onChange,
}: {
  value: FeeFields;
  onChange: (value: FeeFields) => void;
}) {
  const charging = value.payment_mode !== 'none';
  return (
    // Help under the boxes, not between label and box: the three boxes line up.
    <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
      <Select
        label="Payment"
        data={PAYMENT_CHOICES}
        allowDeselect={false}
        value={value.payment_mode}
        onChange={(mode) => {
          onChange({ ...value, payment_mode: (mode ?? 'none') as FeeFields['payment_mode'] });
        }}
      />
      <TextInput
        label="Stripe price ID"
        inputWrapperOrder={BELOW}
        placeholder="price_…"
        description="On a product of the team's own: a recurring price for a subscription, a one-time price for once per period."
        maxLength={100}
        disabled={!charging}
        value={value.stripe_price_id ?? ''}
        onChange={(event) => {
          onChange({ ...value, stripe_price_id: event.currentTarget.value || null });
        }}
      />
      <TextInput
        label="Periods start on"
        inputWrapperOrder={BELOW}
        placeholder="01.10, 01.04"
        description="Day and month. Joining pays the period under way; Stripe charges at each start."
        maxLength={100}
        disabled={!charging}
        value={value.period_starts ?? ''}
        onChange={(event) => {
          onChange({ ...value, period_starts: event.currentTarget.value || null });
        }}
      />
    </SimpleGrid>
  );
}
