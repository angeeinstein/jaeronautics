/**
 * The admins' details of a team, and its fee -- the same fields for a new
 * team and an existing one. What the team says about itself is not here: its
 * leads keep that on the team's own management page.
 */
import { Checkbox, NumberInput, Select, SimpleGrid, Stack, TextInput } from '@mantine/core';

import type { Schemas } from '../../../api/client';
import { ADMISSION_CHOICES, PAYMENT_CHOICES } from './shared';

export type Details = Schemas['TeamDetailsIn'];
export type FeeFields = Schemas['FeeIn'];

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
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
        <TextInput
          label="Name"
          required
          maxLength={120}
          value={value.name}
          error={errors.name}
          onChange={(event) => {
            onChange({ ...value, name: event.currentTarget.value });
          }}
        />
        <Select
          label="Joining"
          data={ADMISSION_CHOICES}
          allowDeselect={false}
          value={value.admission_mode}
          onChange={(mode) => {
            onChange({ ...value, admission_mode: (mode ?? 'approval') as Details['admission_mode'] });
          }}
        />
        <NumberInput
          label="Maximum size"
          description="Empty for no limit."
          min={1}
          allowDecimal={false}
          value={value.max_members ?? ''}
          error={errors.max_members}
          onChange={(size) => {
            onChange({ ...value, max_members: typeof size === 'number' ? size : null });
          }}
        />
        <TextInput
          label="Forum group"
          description="Optional. Its active members are put in this forum group, which has to exist on the forum."
          maxLength={100}
          value={value.forum_group ?? ''}
          error={errors.forum_group}
          onChange={(event) => {
            onChange({ ...value, forum_group: event.currentTarget.value || null });
          }}
        />
      </SimpleGrid>
      <Checkbox
        label="Has rooms that need an access list"
        description="The list of current members for whoever gives access to the team's rooms. Off, the leads do not see it and nothing is sent."
        checked={value.access_list_enabled ?? false}
        onChange={(event) => {
          onChange({ ...value, access_list_enabled: event.currentTarget.checked });
        }}
      />
    </Stack>
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
