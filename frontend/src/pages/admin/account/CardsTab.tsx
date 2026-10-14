/**
 * An account's cards, for admins (``cards.manage``; docs/cards-plan.md): each
 * card with block, lift the block and remove; and linking one for the person,
 * who is there with the admin -- scanned with the admin's phone, its ID typed
 * in, or tapped on a reader while the page waits for it.
 *
 * Data: /api/v1/admin/accounts/<id>/cards, /api/v1/admin/cards/<id>
 * (aeronautics_members/api/cards.py).
 */
import { Button, Group, Select, SimpleGrid, Stack, Text, TextInput } from '@mantine/core';
import { IconNfc } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { api, ApiError, call, type Schemas } from '../../../api/client';
import { type Card, CardList, ScanCard } from '../../../components/cards/CardParts';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Panel } from '../../../components/Panel';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { EVERY_5_SECONDS, EVERY_MINUTE, useLiveRefresh } from '../../../lib/live';
import { notifyDone, notifyFailed } from '../../../lib/notify';

type PersonCards = Schemas['PersonCardsOut'];

function personCardsKey(userId: number) {
  return ['admin', 'cards', 'person', userId] as const;
}

function useRefreshCards(userId: number) {
  const client = useQueryClient();
  return async (data?: PersonCards) => {
    if (data) client.setQueryData(personCardsKey(userId), data);
    await client.invalidateQueries({ queryKey: ['admin', 'cards'], exact: true });
  };
}

function Actions({ card, userId }: { card: Card; userId: number }) {
  const client = useQueryClient();
  const refresh = useRefreshCards(userId);
  const path = { params: { path: { card_id: card.id } } };
  const done = async (message: string) => {
    notifyDone(message);
    await refresh();
    await client.invalidateQueries({ queryKey: personCardsKey(userId) });
  };
  const block = useMutation({
    mutationFn: (blocked: boolean) =>
      call(api.PATCH('/api/v1/admin/cards/{card_id}', { ...path, body: { blocked } })),
    onSuccess: (_card, blocked) =>
      done(blocked ? `${card.name} blocked: refused at every reader.` : `${card.name} unblocked.`),
    onError: notifyFailed,
  });
  const remove = useMutation({
    mutationFn: () => call(api.DELETE('/api/v1/admin/cards/{card_id}', path)),
    onSuccess: () => done(`${card.name} removed.`),
    onError: notifyFailed,
  });
  return (
    <>
      {card.state === 'blocked' ? (
        <Button
          size="xs"
          variant="default"
          loading={block.isPending}
          onClick={() => {
            block.mutate(false);
          }}
        >
          Unblock
        </Button>
      ) : (
        <ConfirmButton
          size="xs"
          variant="default"
          confirmLabel="Yes, block"
          loading={block.isPending}
          onConfirm={() => {
            block.mutate(true);
          }}
        >
          Block
        </ConfirmButton>
      )}
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

function secondsLeft(until: string) {
  return Math.max(0, Math.round((new Date(until).getTime() - Date.now()) / 1000));
}

/** "Tap it on a reader": the next unknown card tapped there within a minute is linked. */
function AtAReader({ cards, userId }: { cards: PersonCards; userId: number }) {
  const refresh = useRefreshCards(userId);
  const [readerId, setReaderId] = useState<string | null>(
    cards.readers[0] ? String(cards.readers[0].id) : null,
  );
  const [, tick] = useState(0);
  const waiting = cards.waiting;
  useEffect(() => {
    if (!waiting) return undefined;
    const timer = window.setInterval(() => {
      tick((n) => n + 1);
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [waiting]);

  const url = { params: { path: { user_id: userId } } };
  const start = useMutation({
    mutationFn: (deviceId: number) =>
      call(
        api.POST('/api/v1/admin/accounts/{user_id}/cards/wait', { ...url, body: { device_id: deviceId } }),
      ),
    onSuccess: (data) => refresh(data),
    onError: notifyFailed,
  });
  const stop = useMutation({
    mutationFn: () => call(api.DELETE('/api/v1/admin/accounts/{user_id}/cards/wait', url)),
    onSuccess: (data) => refresh(data),
    onError: notifyFailed,
  });

  if (!cards.readers.length)
    return (
      <Text size="sm" c="dimmed">
        No reader to tap it on: add one under Settings › Devices.
      </Text>
    );
  if (waiting && secondsLeft(waiting.expires_at) > 0)
    return (
      <Group justify="space-between" gap="md" role="status">
        <Group gap="sm" wrap="nowrap">
          <IconNfc size={24} stroke={1.5} aria-hidden />
          <Text>{`Tap the card on ${waiting.reader.name} (${String(secondsLeft(waiting.expires_at))} s).`}</Text>
        </Group>
        <Button
          variant="default"
          loading={stop.isPending}
          onClick={() => {
            stop.mutate();
          }}
        >
          Stop
        </Button>
      </Group>
    );
  return (
    <Group align="flex-end" gap="sm">
      <Select
        label="Reader"
        data={cards.readers.map((reader) => ({
          value: String(reader.id),
          label: reader.place ? `${reader.name} (${reader.place})` : reader.name,
        }))}
        value={readerId}
        allowDeselect={false}
        onChange={setReaderId}
        style={{ flex: 1, minWidth: 200 }}
      />
      <Button
        variant="default"
        disabled={readerId === null}
        loading={start.isPending}
        onClick={() => {
          if (readerId !== null) start.mutate(Number(readerId));
        }}
      >
        Wait for a tap
      </Button>
    </Group>
  );
}

/** The ID typed in, as a reader or a phone shows it. */
function TypedId({ userId, onLinked }: { userId: number; onLinked: (data: PersonCards) => void }) {
  const [cardId, setCardId] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const link = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/admin/accounts/{user_id}/cards', {
          params: { path: { user_id: userId } },
          body: { card_id: cardId, name: name || null },
        }),
      ),
    onSuccess: (data) => {
      setCardId('');
      setName('');
      setError(null);
      onLinked(data);
    },
    onError: (failure) => {
      if (failure instanceof ApiError && failure.fields.card_id) setError(failure.fields.card_id);
      else notifyFailed(failure);
    },
  });
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        link.mutate();
      }}
    >
      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="sm" style={{ alignItems: 'end' }}>
        <TextInput
          label="Card ID"
          placeholder="04A1B2C3D4E5F6"
          styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)' } }}
          maxLength={40}
          value={cardId}
          error={error}
          onChange={(event) => {
            setCardId(event.currentTarget.value);
          }}
        />
        <TextInput
          label="Name (optional)"
          placeholder="Student card"
          maxLength={40}
          value={name}
          onChange={(event) => {
            setName(event.currentTarget.value);
          }}
        />
        <Button type="submit" variant="default" loading={link.isPending} disabled={!cardId.trim()}>
          Link
        </Button>
      </SimpleGrid>
    </form>
  );
}

function LinkForThem({ cards, userId }: { cards: PersonCards; userId: number }) {
  const refresh = useRefreshCards(userId);
  // "Card linked." is said by the tab, which sees every new card, however it came.
  const linked = (data: PersonCards) => {
    void refresh(data);
  };
  const scan = useMutation({
    mutationFn: ({ cardId, name }: { cardId: string; name: string }) =>
      call(
        api.POST('/api/v1/admin/accounts/{user_id}/cards', {
          params: { path: { user_id: userId } },
          body: { card_id: cardId, name: name || null },
        }),
      ),
    onSuccess: linked,
    onError: notifyFailed,
  });
  if (cards.items.length >= cards.most)
    return (
      <Panel title="Link a card">
        <Text c="dimmed">{`${String(cards.most)} cards, the most anyone may have.`}</Text>
      </Panel>
    );
  return (
    <Panel title="Link a card">
      <Stack gap="lg">
        <Text size="sm" c="dimmed">
          With the person there: tap their card on a reader, scan it with your phone, or type its ID.
        </Text>
        <AtAReader cards={cards} userId={userId} />
        <ScanCard
          busy={scan.isPending}
          onScanned={(cardId, name) => {
            scan.mutate({ cardId, name });
          }}
          unsupported={null}
        />
        <TypedId userId={userId} onLinked={linked} />
      </Stack>
    </Panel>
  );
}

export function CardsTab({ userId }: { userId: number }) {
  const key = personCardsKey(userId);
  const cards = useQuery({
    queryKey: key,
    queryFn: () =>
      call(api.GET('/api/v1/admin/accounts/{user_id}/cards', { params: { path: { user_id: userId } } })),
  });
  const waiting = cards.data?.waiting ?? null;
  // Quickly while a reader waits for the card, so the linked card shows as it is tapped.
  useLiveRefresh(key, waiting ? EVERY_5_SECONDS : EVERY_MINUTE);
  const count = cards.data?.items.length;
  const before = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (count === undefined) return;
    if (before.current !== undefined && count > before.current) notifyDone('Card linked.');
    before.current = count;
  }, [count]);

  if (cards.isPending) return <LoadingState />;
  if (cards.isError) return <ErrorState error={cards.error} onRetry={() => void cards.refetch()} />;
  return (
    <Stack gap="lg">
      <Panel title="Cards" flush>
        {cards.data.items.length ? (
          <CardList cards={cards.data.items}>{(card) => <Actions card={card} userId={userId} />}</CardList>
        ) : (
          <Stack px="md">
            <EmptyState>No card linked.</EmptyState>
          </Stack>
        )}
      </Panel>
      <LinkForThem cards={cards.data} userId={userId} />
    </Stack>
  );
}
