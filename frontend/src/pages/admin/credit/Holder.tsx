/**
 * Admin › Credit › one person (docs/credit-plan.md): their balance, booking
 * by hand -- cash handed over, credit paid out, a correction with its
 * reason -- refunding through Stripe, and every entry with who booked it.
 * Data: GET /api/v1/admin/credit/<user_id> and its POSTs (cash, correction,
 * refund), aeronautics_members/api/credit.py.
 */
import {
  Alert,
  Avatar,
  Button,
  Group,
  NumberInput,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
} from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useParams } from 'react-router';

import { api, ApiError, call } from '../../../api/client';
import { AppLink } from '../../../app/AppLink';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { ErrorState, LoadingState } from '../../../components/States';
import { formatEuros } from '../../../lib/format';
import { EVERY_MINUTE, useLiveRefresh } from '../../../lib/live';
import { notifyDone, notifyFailed, notifyNote } from '../../../lib/notify';
import { Balance, History } from '../../account/Credit';
import { type CreditHolder, holderQuery, initialsOf } from './shared';

type Booking = 'sale' | 'cash_in' | 'cash_out' | 'correction';

const BOOKINGS: { label: string; value: Booking }[] = [
  { label: 'Sale', value: 'sale' },
  { label: 'Cash in', value: 'cash_in' },
  { label: 'Paid out', value: 'cash_out' },
  { label: 'Correction', value: 'correction' },
];

const HINTS: Record<Booking, string> = {
  sale: 'Something from a price list, at its price; it counts for whoever sells it.',
  cash_in: 'Money handed over, added to the credit.',
  cash_out: 'Credit given back in cash or by transfer.',
  correction: 'Above zero adds, below zero takes off. Say why.',
};

function useHolderChange(userId: number) {
  const client = useQueryClient();
  return (holder: CreditHolder) => {
    client.setQueryData(holderQuery(userId).queryKey, holder);
    void client.invalidateQueries({ queryKey: ['admin', 'credit'], exact: true });
  };
}

function Book({ holder }: { holder: CreditHolder }) {
  const userId = holder.user_id;
  const sells = holder.items.length > 0;
  const [kind, setKind] = useState<Booking>('cash_in');
  const [itemId, setItemId] = useState<string | null>(null);
  const [amount, setAmount] = useState<number | ''>('');
  const [note, setNote] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const take = useHolderChange(userId);
  const book = useMutation({
    mutationFn: () => {
      const cents = Math.round(Number(amount) * 100);
      const path = { user_id: userId };
      if (kind === 'sale')
        return call(
          api.POST('/api/v1/admin/credit/{user_id}/sale', {
            params: { path },
            body: { item_id: Number(itemId) },
          }),
        );
      if (kind === 'correction')
        return call(
          api.POST('/api/v1/admin/credit/{user_id}/correction', {
            params: { path },
            body: { amount_cents: cents, note },
          }),
        );
      return call(
        api.POST('/api/v1/admin/credit/{user_id}/cash', {
          params: { path },
          body: { amount_cents: cents, paid_out: kind === 'cash_out', note: note || null },
        }),
      );
    },
    onSuccess: (out) => {
      take(out);
      setAmount('');
      setNote('');
      setItemId(null);
      setErrors({});
      notifyDone('Booked.');
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const ready =
    kind === 'sale'
      ? itemId !== null
      : amount !== '' && amount !== 0 && (kind !== 'correction' || note.trim() !== '');
  return (
    <Panel title="Book by hand">
      <Stack gap="md">
        <SegmentedControl
          data={sells ? BOOKINGS : BOOKINGS.filter((booking) => booking.value !== 'sale')}
          value={kind}
          onChange={(value) => {
            setKind(value);
            setErrors({});
          }}
          aria-label="What to book"
        />
        <Text size="sm" c="dimmed">
          {HINTS[kind]}
        </Text>
        {kind === 'sale' ? (
          <Select
            label="Item"
            placeholder="Choose what was sold"
            searchable
            data={holder.items.map((item) => ({
              value: String(item.id),
              label: `${item.name} · ${formatEuros(item.price_cents)} · ${item.seller}`,
            }))}
            value={itemId}
            error={errors.item_id}
            onChange={(value) => {
              setItemId(value);
              setErrors({});
            }}
          />
        ) : (
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <NumberInput
              label="Amount"
              prefix="€"
              decimalScale={2}
              allowNegative={kind === 'correction'}
              value={amount}
              error={errors.amount_cents}
              onChange={(value) => {
                setAmount(typeof value === 'number' ? value : value === '' ? '' : Number(value));
                setErrors({});
              }}
            />
            <TextInput
              label={kind === 'correction' ? 'Why' : 'Note'}
              placeholder={kind === 'cash_in' ? 'Cash top-up' : kind === 'cash_out' ? 'Paid out' : ''}
              maxLength={140}
              value={note}
              error={errors.note}
              onChange={(event) => {
                setNote(event.currentTarget.value);
                setErrors({});
              }}
            />
          </SimpleGrid>
        )}
        <Group justify="flex-end">
          <Button
            disabled={!ready || (holder.erased && kind !== 'cash_out')}
            loading={book.isPending}
            onClick={() => {
              book.mutate();
            }}
          >
            Book
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

function Refund({ holder }: { holder: CreditHolder }) {
  const take = useHolderChange(holder.user_id);
  const refund = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/admin/credit/{user_id}/refund', { params: { path: { user_id: holder.user_id } } }),
      ),
    onSuccess: (out) => {
      take(out);
      if (out.refunded_cents) notifyDone(`${formatEuros(out.refunded_cents)} refunded.`);
      if (out.left_cents)
        notifyNote(`${formatEuros(out.left_cents)} is left: pay it out by hand and book it as paid out.`);
    },
    onError: notifyFailed,
  });
  const balance = Math.max(holder.balance_cents, 0);
  const byHand = balance - holder.refundable_cents;
  if (balance === 0) return null;
  return (
    <Panel title="Refund">
      <Stack gap="sm">
        <Text size="sm">
          {holder.refundable_cents
            ? `${formatEuros(holder.refundable_cents)} can go back to the card or account it was paid with, newest top-up first. Stripe never refunds more than a payment was.`
            : 'Nothing can go back through Stripe: the credit is cash, or its payments are too old.'}
        </Text>
        {byHand > 0 ? (
          <Text size="sm" c="dimmed">
            {`${formatEuros(byHand)} is cash or too old for Stripe: pay it out by hand and book it as paid out.`}
          </Text>
        ) : null}
        {holder.refundable_cents ? (
          <Group justify="flex-end">
            <ConfirmButton
              variant="default"
              confirmLabel={`Yes, refund ${formatEuros(holder.refundable_cents)}`}
              loading={refund.isPending}
              onConfirm={() => {
                refund.mutate();
              }}
            >
              {`Refund ${formatEuros(holder.refundable_cents)}`}
            </ConfirmButton>
          </Group>
        ) : null}
      </Stack>
    </Panel>
  );
}

function Body({ holder }: { holder: CreditHolder }) {
  const take = useHolderChange(holder.user_id);
  const back = useMutation({
    mutationFn: (entryId: number) =>
      call(
        api.POST('/api/v1/admin/credit/{user_id}/entries/{entry_id}/take-back', {
          params: { path: { user_id: holder.user_id, entry_id: entryId } },
          body: { note: null },
        }),
      ),
    onSuccess: (out) => {
      take(out);
      notifyDone('Taken back.');
    },
    onError: notifyFailed,
  });
  const takeBack = (entryId: number) => {
    back.mutate(entryId);
  };
  return (
    <Stack gap="lg">
      {holder.erased ? (
        <Alert color="gray" variant="light">
          This account was erased. What is left can only be paid out.
        </Alert>
      ) : null}
      <Balance
        cents={holder.balance_cents}
        maxCents={holder.max_balance_cents}
        label="Credit"
        note={
          holder.member
            ? `At most ${formatEuros(holder.max_balance_cents)} on one account.`
            : 'Not an active member: cannot top up.'
        }
      />
      <Book holder={holder} />
      <Refund holder={holder} />
      <History entries={holder.entries} onTakeBack={takeBack} />
    </Stack>
  );
}

export function Holder() {
  const { userId } = useParams();
  const id = Number(userId);
  const holder = useQuery({ ...holderQuery(id), enabled: Number.isInteger(id) });
  useLiveRefresh(holderQuery(id).queryKey, EVERY_MINUTE, Number.isInteger(id));
  const name = holder.data?.name ?? 'Credit';
  return (
    <>
      <PageHeader
        title={name}
        description={holder.data?.email ?? undefined}
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Credit', to: '/admin/credit' }, { label: name }]}
        leading={
          holder.data ? (
            <Avatar src={holder.data.picture_url} alt="" size={56} color="brand" variant="filled">
              {initialsOf(holder.data.name)}
            </Avatar>
          ) : undefined
        }
        actions={
          holder.data?.account_url ? (
            <Button component={AppLink} to={holder.data.account_url} variant="default">
              Account
            </Button>
          ) : holder.data?.erased ? (
            <Pill tone="neutral">Erased</Pill>
          ) : undefined
        }
      />
      {holder.isPending ? (
        <LoadingState />
      ) : holder.isError ? (
        <ErrorState error={holder.error} onRetry={() => void holder.refetch()} />
      ) : (
        <Body holder={holder.data} />
      )}
    </>
  );
}
