/**
 * What is hard or impossible to undo: switching the account off (or back on)
 * and erasing the person's data. Each says what it does, and what forbids
 * it, before anybody confirms.
 */
import { Alert, Button, Group, List, Stack, Text, TextInput } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Panel } from '../../../components/Panel';
import { ErrorState, LoadingState } from '../../../components/States';
import { formatDate, formatDayOf, formatEuros, plural } from '../../../lib/format';
import { notifyDone, notifyNote } from '../../../lib/notify';
import { type Account, erasureQuery, path, useAccountAction } from './shared';

function SwitchOff({ account }: { account: Account }) {
  const [reason, setReason] = useState('');
  const blockers = account.access?.disable_blockers ?? [];
  const change = useAccountAction(
    (body: { disabled: boolean; reason?: string }) =>
      call(api.PUT('/api/v1/admin/accounts/{user_id}/disabled', { ...path(account.id), body })),
    {
      done: (answer) => {
        if (!answer.changed) notifyNote('Nothing changed.');
        else if (account.disabled_at) notifyDone('The account can be used again.');
        else notifyDone('The account is deactivated. The membership is unchanged.');
        setReason('');
      },
    },
  );

  if (account.disabled_at) {
    return (
      <Panel title="Deactivated">
        <Stack gap="sm" align="flex-start">
          <Text size="sm">
            Cannot sign in or use the forum{account.disabled_reason ? `: ${account.disabled_reason}` : '.'}
          </Text>
          <Button
            variant="default"
            loading={change.isPending}
            onClick={() => {
              change.mutate({ disabled: false });
            }}
          >
            Reactivate
          </Button>
        </Stack>
      </Panel>
    );
  }
  return (
    <Panel title="Deactivate">
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          Stops them signing in and removes their forum access. The membership, including anything paid for,
          stays exactly as it is.
        </Text>
        {blockers.length ? (
          blockers.map((blocker) => (
            <Alert key={blocker} variant="light">
              {blocker}
            </Alert>
          ))
        ) : (
          <Group align="flex-end" gap="sm">
            <TextInput
              label="Reason"
              placeholder="Why is this account being deactivated?"
              maxLength={255}
              value={reason}
              onChange={(event) => {
                setReason(event.currentTarget.value);
              }}
              flex="1 1 18rem"
            />
            <ConfirmButton
              confirmLabel="Yes, deactivate"
              loading={change.isPending}
              onConfirm={() => {
                change.mutate({ disabled: true, reason: reason.trim() || undefined });
              }}
            >
              Deactivate
            </ConfirmButton>
          </Group>
        )}
      </Stack>
    </Panel>
  );
}

function Erase({ account }: { account: Account }) {
  const erasure = useQuery(erasureQuery(account.id));
  const [reason, setReason] = useState('');
  const [typed, setTyped] = useState('');
  const erase = useAccountAction(
    () =>
      call(
        api.POST('/api/v1/admin/accounts/{user_id}/erase', {
          ...path(account.id),
          body: { confirm_email: typed.trim(), reason: reason.trim() || null },
        }),
      ),
    {
      done: (answer) => {
        notifyDone('The personal data is erased.');
        if (answer.subscription_cancelled)
          notifyNote('The Stripe subscription was cancelled. Nothing was refunded.');
        if (answer.forum_deferred)
          notifyNote('The forum could not be reached; its account is anonymised as soon as it can be.');
      },
    },
  );

  if (account.erased_at) {
    return (
      <Panel title="Personal data">
        <Text size="sm">
          Erased on {formatDayOf(account.erased_at)}. The membership and payment record was kept.
        </Text>
      </Panel>
    );
  }
  return (
    <Panel title="Erase personal data">
      {erasure.isPending ? (
        <LoadingState />
      ) : erasure.isError ? (
        <ErrorState error={erasure.error} onRetry={() => void erasure.refetch()} />
      ) : erasure.data.blockers.length ? (
        <Stack gap="sm">
          {erasure.data.blockers.map((blocker) => (
            <Alert key={blocker} variant="light">
              {blocker}
            </Alert>
          ))}
        </Stack>
      ) : (
        <Stack gap="md">
          <Text size="sm">This cannot be undone. It does this:</Text>
          <List size="sm" spacing={4}>
            <List.Item>Name, address, phone numbers and email addresses are overwritten.</List.Item>
            <List.Item>The login stops working and every role is removed.</List.Item>
            <List.Item>
              Membership periods and payments are kept without personal details, as bookkeeping law requires.
              {erasure.data.paid_periods
                ? ` ${String(erasure.data.paid_periods)} paid ${plural(erasure.data.paid_periods, 'period stays', 'periods stay')}.`
                : ''}
            </List.Item>
            {erasure.data.has_forum_account ? (
              <List.Item>The forum account is anonymised. Posts stay, under an anonymous name.</List.Item>
            ) : null}
            {erasure.data.has_stripe_customer ? (
              <List.Item>
                The customer at Stripe stays: it is the association&apos;s accounting record and the only link
                left between this erased account and who paid.
              </List.Item>
            ) : null}
          </List>
          {erasure.data.subscription_active ? (
            <Alert color="amber" variant="light" title="An active subscription">
              It is cancelled at once, without a refund. To refund the unused part of the year, do that in
              Stripe first.
              {erasure.data.subscription_status ? ` Stripe says: ${erasure.data.subscription_status}.` : ''}
            </Alert>
          ) : null}
          {erasure.data.coverage_end ? (
            <Alert color="amber" variant="light">
              Paid until {formatDate(erasure.data.coverage_end)}. Erasing ends the access now.
            </Alert>
          ) : null}
          {erasure.data.credit_cents ? (
            <Alert color="brand" variant="light">
              {`${formatEuros(erasure.data.credit_cents)} of credit is refunded to the payments it came from. What was cash, the admins are told to pay out by hand.`}
            </Alert>
          ) : null}
          <TextInput
            label="Reason (kept in the log)"
            placeholder="For example: asked for by the member, or expulsion decided on …"
            maxLength={255}
            value={reason}
            onChange={(event) => {
              setReason(event.currentTarget.value);
            }}
          />
          <TextInput
            label={`Type ${erasure.data.confirm_email} to confirm`}
            autoComplete="off"
            value={typed}
            onChange={(event) => {
              setTyped(event.currentTarget.value);
            }}
          />
          <Group>
            <Button
              color="red"
              variant="outline"
              loading={erase.isPending}
              disabled={typed.trim().toLowerCase() !== erasure.data.confirm_email.toLowerCase()}
              onClick={() => {
                erase.mutate(undefined);
              }}
            >
              Erase personal data
            </Button>
          </Group>
        </Stack>
      )}
    </Panel>
  );
}

export function DangerZoneTab({ account }: { account: Account }) {
  return (
    <Stack gap="lg">
      {account.actions.manage_access ? <SwitchOff account={account} /> : null}
      {account.actions.erase ? <Erase account={account} /> : null}
    </Stack>
  );
}
