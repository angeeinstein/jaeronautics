/**
 * My Account › Cards (docs/cards-plan.md): one's cards for the readers --
 * a student card, a key fob -- each with its name, the end of its ID, its
 * state and its last use; renamed, switched off and on, removed here. A new
 * one is linked by scanning it with the phone (Web NFC, Chrome on Android);
 * elsewhere, an admin links it.
 *
 * Data: /api/v1/account/cards (aeronautics_members/api/cards.py).
 */
import { ActionIcon, Group, Stack, Switch, Text, TextInput, Tooltip } from '@mantine/core';
import { IconCheck, IconPencil, IconX } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call, type Schemas } from '../../api/client';
import { type Card, CardList, ScanCard } from '../../components/cards/CardParts';
import { ConfirmButton } from '../../components/ConfirmButton';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import { EVERY_30_SECONDS, useLiveRefresh } from '../../lib/live';
import { notifyDone, notifyFailed } from '../../lib/notify';

type MyCards = Schemas['MyCardsOut'];

const cardsKey = ['account', 'cards'] as const;

function useSaveCards() {
  const client = useQueryClient();
  return (data: MyCards) => {
    client.setQueryData(cardsKey, data);
  };
}

function Rename({ card, onDone }: { card: Card; onDone: () => void }) {
  const save = useSaveCards();
  const [name, setName] = useState(card.name);
  const rename = useMutation({
    mutationFn: () =>
      call(
        api.PATCH('/api/v1/account/cards/{card_id}', {
          params: { path: { card_id: card.id } },
          body: { name },
        }),
      ),
    onSuccess: (data) => {
      save(data);
      onDone();
    },
    onError: notifyFailed,
  });
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        rename.mutate();
      }}
    >
      <Group gap="xs" wrap="nowrap">
        <TextInput
          aria-label="Name"
          size="xs"
          maxLength={40}
          value={name}
          autoFocus
          onChange={(event) => {
            setName(event.currentTarget.value);
          }}
        />
        <ActionIcon type="submit" variant="default" aria-label="Save the name" loading={rename.isPending}>
          <IconCheck size={16} />
        </ActionIcon>
        <ActionIcon variant="subtle" color="gray" aria-label="Cancel" onClick={onDone}>
          <IconX size={16} />
        </ActionIcon>
      </Group>
    </form>
  );
}

function Actions({ card }: { card: Card }) {
  const save = useSaveCards();
  const [renaming, setRenaming] = useState(false);
  const path = { params: { path: { card_id: card.id } } };
  const change = useMutation({
    mutationFn: (enabled: boolean) =>
      call(api.PATCH('/api/v1/account/cards/{card_id}', { ...path, body: { enabled } })),
    onSuccess: (data, enabled) => {
      save(data);
      notifyDone(
        enabled ? `${card.name} is on.` : `${card.name} is off: readers refuse it until you switch it on.`,
      );
    },
    onError: notifyFailed,
  });
  const remove = useMutation({
    mutationFn: () => call(api.DELETE('/api/v1/account/cards/{card_id}', path)),
    onSuccess: (data) => {
      save(data);
      notifyDone(`${card.name} removed.`);
    },
    onError: notifyFailed,
  });

  if (renaming)
    return (
      <Rename
        card={card}
        onDone={() => {
          setRenaming(false);
        }}
      />
    );
  return (
    <>
      <Tooltip label="Blocked by an admin" disabled={card.state !== 'blocked'}>
        <Switch
          label="On"
          checked={card.state === 'active'}
          disabled={card.state === 'blocked' || change.isPending}
          onChange={(event) => {
            change.mutate(event.currentTarget.checked);
          }}
        />
      </Tooltip>
      <ActionIcon
        variant="subtle"
        color="gray"
        aria-label={`Rename ${card.name}`}
        onClick={() => {
          setRenaming(true);
        }}
      >
        <IconPencil size={16} />
      </ActionIcon>
      <ConfirmButton
        size="xs"
        variant="default"
        confirmLabel="Yes, remove"
        loading={remove.isPending}
        onConfirm={() => {
          remove.mutate();
        }}
      >
        Remove
      </ConfirmButton>
    </>
  );
}

function LinkCard({ cards }: { cards: MyCards }) {
  const save = useSaveCards();
  const link = useMutation({
    mutationFn: ({ cardId, name }: { cardId: string; name: string }) =>
      call(api.POST('/api/v1/account/cards', { body: { card_id: cardId, name: name || null } })),
    onSuccess: (data) => {
      save(data);
      notifyDone('Card linked.');
    },
    onError: notifyFailed,
  });
  if (cards.items.length >= cards.most)
    return (
      <Panel title="Link a card">
        <Text c="dimmed">{`You have ${String(cards.most)} cards, the most anyone may have. Remove one first.`}</Text>
      </Panel>
    );
  return (
    <Panel title="Link a card">
      <ScanCard
        busy={link.isPending}
        onScanned={(cardId, name) => {
          link.mutate({ cardId, name });
        }}
        unsupported={
          <Text c="dimmed">
            This browser cannot read cards. Open this page in Chrome on an Android phone, or ask an admin to
            link your card.
          </Text>
        }
      />
    </Panel>
  );
}

export function Cards() {
  const cards = useQuery({
    queryKey: cardsKey,
    queryFn: () => call(api.GET('/api/v1/account/cards')),
  });
  // Its last use changes with every tap at a reader.
  useLiveRefresh(cardsKey, EVERY_30_SECONDS);
  return (
    <>
      <PageHeader
        title="Cards"
        description="Your cards for the readers: tap one to pay at the fridge."
        crumbs={[{ label: 'My Account', to: '/account' }, { label: 'Cards' }]}
      />
      {cards.isPending ? (
        <LoadingState />
      ) : cards.isError ? (
        <ErrorState error={cards.error} onRetry={() => void cards.refetch()} />
      ) : (
        <Stack gap="lg">
          <Panel title="Your cards" flush>
            {cards.data.items.length ? (
              <CardList cards={cards.data.items}>{(card) => <Actions card={card} />}</CardList>
            ) : (
              <Stack px="md">
                <EmptyState>No card linked yet.</EmptyState>
              </Stack>
            )}
          </Panel>
          <LinkCard cards={cards.data} />
        </Stack>
      )}
    </>
  );
}
