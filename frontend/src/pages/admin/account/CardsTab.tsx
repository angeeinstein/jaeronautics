/**
 * An account's cards, for admins (``cards.manage``; docs/cards-plan.md): each
 * card with block, lift the block and remove. Its owner links a card at a
 * reader, with the QR code shown there; admins do not link cards.
 *
 * Data: /api/v1/admin/accounts/<id>/cards, /api/v1/admin/cards/<id>
 * (aeronautics_members/api/cards.py).
 */
import { Button, Stack } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { IconNfc } from '@tabler/icons-react';

import { api, call, type Schemas } from '../../../api/client';
import { type Card, CardList } from '../../../components/cards/CardParts';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Panel } from '../../../components/Panel';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { EVERY_MINUTE, useLiveRefresh } from '../../../lib/live';
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

export function CardsTab({ userId }: { userId: number }) {
  const key = personCardsKey(userId);
  const cards = useQuery({
    queryKey: key,
    queryFn: () =>
      call(api.GET('/api/v1/admin/accounts/{user_id}/cards', { params: { path: { user_id: userId } } })),
  });
  useLiveRefresh(key, EVERY_MINUTE);

  if (cards.isPending) return <LoadingState />;
  if (cards.isError) return <ErrorState error={cards.error} onRetry={() => void cards.refetch()} />;
  return (
    <Panel icon={IconNfc} title="Cards" flush>
      {cards.data.items.length ? (
        <CardList cards={cards.data.items}>{(card) => <Actions card={card} userId={userId} />}</CardList>
      ) : (
        <Stack px="md">
          <EmptyState>No card linked. Its owner links one by tapping it on a sales point.</EmptyState>
        </Stack>
      )}
    </Panel>
  );
}
