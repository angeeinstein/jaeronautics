/**
 * My Account › Membership: its state, the dates, what to do about it -- and
 * the ways to Stripe: the billing page (payment method, invoices, cancelling),
 * a payment not finished, or rejoining after the membership ended. While a
 * payment just made is being confirmed, the card asks again every few seconds.
 */
import { Button, Group, Radio, Stack } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call, type Schemas } from '../../api/client';
import { Details } from '../../components/Details';
import { Panel } from '../../components/Panel';
import { Pill } from '../../components/Pill';
import { formatDate } from '../../lib/format';
import { notifyFailed, notifyNote } from '../../lib/notify';
import { goTo, Note } from './shared';

type Membership = Schemas['MembershipCardOut'];

export function useBilling() {
  return useMutation({
    mutationFn: () => call(api.POST('/api/v1/account/billing')),
    onSuccess: ({ url }) => {
      goTo(url);
    },
    onError: notifyFailed,
  });
}

function Rejoin({ invoicePayments }: { invoicePayments: boolean }) {
  const [method, setMethod] = useState<'checkout' | 'invoice'>('checkout');
  const rejoin = useMutation({
    mutationFn: () => call(api.POST('/api/v1/account/rejoin', { body: { payment_method: method } })),
    onSuccess: ({ url, message }) => {
      if (url) goTo(url);
      else if (message) notifyNote(message);
    },
    onError: notifyFailed,
  });
  return (
    <Stack gap="sm">
      {invoicePayments ? (
        <Radio.Group
          label="Pay by"
          value={method}
          onChange={(value) => {
            setMethod(value === 'invoice' ? 'invoice' : 'checkout');
          }}
        >
          <Group gap="lg" mt={4}>
            <Radio value="checkout" label="Card or SEPA Direct Debit" />
            <Radio value="invoice" label="Invoice" />
          </Group>
        </Radio.Group>
      ) : null}
      <Group>
        <Button
          loading={rejoin.isPending}
          onClick={() => {
            rejoin.mutate();
          }}
        >
          Rejoin
        </Button>
      </Group>
    </Stack>
  );
}

export function MembershipCard({ membership }: { membership: Membership }) {
  const billing = useBilling();
  const resume = useMutation({
    mutationFn: () => call(api.POST('/api/v1/account/payment')),
    onSuccess: ({ url }) => {
      goTo(url);
    },
    onError: notifyFailed,
  });
  return (
    <Panel title="Membership" actions={<Pill tone={membership.tone}>{membership.status_label}</Pill>}>
      <Stack gap="md">
        <Details
          compact
          items={[
            ['Active', membership.active ? 'Yes' : 'No'],
            ['Since', membership.starts_on ? formatDate(membership.starts_on) : null],
            ['Paid until', membership.ends_on ? formatDate(membership.ends_on) : null],
            [
              'Renews',
              membership.renews_on ? formatDate(membership.renews_on) : membership.auto_renew ? null : 'No',
            ],
          ]}
        />
        {membership.note ? <Note note={membership.note} /> : null}
        {membership.may_rejoin ? <Rejoin invoicePayments={membership.invoice_payments} /> : null}
        {membership.may_resume_payment || membership.may_manage_billing ? (
          <Group>
            {membership.may_resume_payment ? (
              <Button
                loading={resume.isPending}
                onClick={() => {
                  resume.mutate();
                }}
              >
                Resume payment
              </Button>
            ) : null}
            {membership.may_manage_billing ? (
              <Button
                variant={membership.may_resume_payment || membership.may_rejoin ? 'default' : 'filled'}
                loading={billing.isPending}
                onClick={() => {
                  billing.mutate();
                }}
              >
                Manage billing
              </Button>
            ) : null}
          </Group>
        ) : null}
      </Stack>
    </Panel>
  );
}
