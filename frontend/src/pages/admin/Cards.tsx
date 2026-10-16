/**
 * Admin › Cards (docs/cards-plan.md): every card linked now, newest first --
 * whose it is, its name, the end of its ID, its state, its last use. A card
 * is managed on its owner's page (the account's Cards tab), linked from each
 * row. Few enough to be filtered on the page.
 *
 * Data: GET /api/v1/admin/cards (aeronautics_members/api/cards.py).
 */
import { Avatar, Group, Stack, Table, Text, TextInput } from '@mantine/core';
import { IconNfc, IconSearch } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { CARD_STATE, lastUse } from '../../components/cards/CardParts';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { Pill } from '../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import { formatDateTime } from '../../lib/format';
import { EVERY_MINUTE, useLiveRefresh } from '../../lib/live';

const cardsKey = ['admin', 'cards'] as const;

export function Cards() {
  const cards = useQuery({ queryKey: cardsKey, queryFn: () => call(api.GET('/api/v1/admin/cards')) });
  useLiveRefresh(cardsKey, EVERY_MINUTE);
  const [query, setQuery] = useState('');
  const wanted = query.trim().toLowerCase();
  const shown = (cards.data?.items ?? []).filter(
    (card) =>
      !wanted ||
      [card.owner.name, card.owner.email, card.name, card.tail].some((text) =>
        text.toLowerCase().includes(wanted),
      ),
  );

  return (
    <>
      <PageHeader
        title="Cards"
        description="Every card linked to an account. Block, remove or link one on the person's page."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Cards' }]}
      />
      {cards.isPending ? (
        <LoadingState />
      ) : cards.isError ? (
        <ErrorState error={cards.error} onRetry={() => void cards.refetch()} />
      ) : (
        <Panel
          icon={IconNfc}
          title={`${String(cards.data.items.length)} ${cards.data.items.length === 1 ? 'card' : 'cards'}`}
          flush
          actions={
            cards.data.items.length ? (
              <TextInput
                aria-label="Search cards"
                placeholder="Person, card or its end"
                size="xs"
                leftSection={<IconSearch size={14} aria-hidden />}
                value={query}
                onChange={(event) => {
                  setQuery(event.currentTarget.value);
                }}
              />
            ) : null
          }
        >
          {shown.length ? (
            <Table.ScrollContainer minWidth={640} type="native">
              <Table verticalSpacing="sm" aria-label="Cards">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th scope="col">Person</Table.Th>
                    <Table.Th scope="col">Card</Table.Th>
                    <Table.Th scope="col">State</Table.Th>
                    <Table.Th scope="col">Linked</Table.Th>
                    <Table.Th scope="col">Last used</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {shown.map((card) => (
                    <Table.Tr key={card.id}>
                      <Table.Td>
                        <Group gap="sm" wrap="nowrap">
                          <Avatar src={card.owner.picture_url} alt="" size={28} radius="xl" aria-hidden />
                          <AppLink to={`/admin/accounts/${String(card.owner.user_id)}#cards`}>
                            {card.owner.name}
                          </AppLink>
                        </Group>
                      </Table.Td>
                      <Table.Td>
                        <Group gap="xs" wrap="nowrap">
                          <Text size="sm">{card.name}</Text>
                          <Text span size="sm" c="dimmed" ff="monospace">
                            {card.tail}
                          </Text>
                        </Group>
                      </Table.Td>
                      <Table.Td>
                        <Pill tone={CARD_STATE[card.state].tone}>{CARD_STATE[card.state].label}</Pill>
                      </Table.Td>
                      <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateTime(card.linked_at)}</Table.Td>
                      <Table.Td>
                        <Text size="sm" c={card.last_used_at ? undefined : 'dimmed'}>
                          {lastUse(card).replace(/^Last used /, '')}
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          ) : (
            <Stack px="md">
              <EmptyState>{cards.data.items.length ? 'No card matches.' : 'No card linked yet.'}</EmptyState>
            </Stack>
          )}
        </Panel>
      )}
    </>
  );
}
