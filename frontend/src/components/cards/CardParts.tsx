/**
 * What My Account › Cards and an admin's Cards tab share (docs/cards-plan.md):
 * a card's row -- its name, the end of its ID, its state, its last use.
 */
import { Group, Stack, Text } from '@mantine/core';
import { IconId } from '@tabler/icons-react';
import type { ReactNode } from 'react';

import type { Schemas } from '../../api/client';
import { formatDateTime } from '../../lib/format';
import { Pill, type Tone } from '../Pill';
import classes from './Cards.module.css';

export type Card = Schemas['CardOut'];

export const CARD_STATE: Record<Card['state'], { label: string; tone: Tone }> = {
  active: { label: 'Active', tone: 'active' },
  disabled: { label: 'Off', tone: 'neutral' },
  blocked: { label: 'Blocked', tone: 'failed' },
  removed: { label: 'Removed', tone: 'neutral' },
};

export function lastUse(card: Card): string {
  if (!card.last_used_at) return 'Not used yet';
  const when = `Last used ${formatDateTime(card.last_used_at)}`;
  return card.last_used_on ? `${when}, ${card.last_used_on}` : when;
}

/** One card, with what may be done to it on the right. */
export function CardRow({ card, actions }: { card: Card; actions?: ReactNode }) {
  return (
    <li className={classes.row} aria-label={card.name}>
      <IconId size={22} stroke={1.5} className={classes.icon} aria-hidden />
      <Stack gap={2} miw={0} className={classes.main}>
        <Group gap="xs" wrap="wrap">
          <Text fw={600}>{card.name}</Text>
          <Text span size="sm" c="dimmed" ff="monospace">
            {card.tail}
          </Text>
          <Pill tone={CARD_STATE[card.state].tone}>{CARD_STATE[card.state].label}</Pill>
        </Group>
        <Text size="sm" c="dimmed">
          {card.removed
            ? 'Removed by its owner, still blocked: nobody links it again until the block is lifted.'
            : card.state === 'blocked'
              ? `Blocked by an admin. ${lastUse(card)}`
              : lastUse(card)}
        </Text>
      </Stack>
      {actions ? <div className={classes.actions}>{actions}</div> : null}
    </li>
  );
}

export function CardList({ cards, children }: { cards: Card[]; children: (card: Card) => ReactNode }) {
  return (
    <ul className={classes.list} aria-label="Cards">
      {cards.map((card) => (
        <CardRow key={card.id} card={card} actions={children(card)} />
      ))}
    </ul>
  );
}
