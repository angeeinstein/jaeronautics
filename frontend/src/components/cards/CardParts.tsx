/**
 * What My Account › Cards and an admin's Cards tab share (docs/cards-plan.md):
 * a card's row -- its name, the end of its ID, its state, its last use -- and
 * scanning a card with the phone (Web NFC: Chrome on Android).
 */
import { Button, Group, Stack, Text, TextInput } from '@mantine/core';
import { IconId, IconNfc } from '@tabler/icons-react';
import { type ReactNode, useEffect, useRef, useState } from 'react';

import type { Schemas } from '../../api/client';
import { formatDateTime } from '../../lib/format';
import { notifyNote } from '../../lib/notify';
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
          {card.state === 'blocked' ? `Blocked by an admin. ${lastUse(card)}` : lastUse(card)}
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

// --- Scanning with the phone (Web NFC) ---------------------------------------------------

interface NdefReadingEvent extends Event {
  serialNumber: string;
}

interface NdefReader extends EventTarget {
  scan(options?: { signal?: AbortSignal }): Promise<void>;
  onreading: ((event: NdefReadingEvent) => void) | null;
  onreadingerror: ((event: Event) => void) | null;
}

type NdefReaderConstructor = new () => NdefReader;

function ndefReader(): NdefReaderConstructor | undefined {
  return (window as unknown as { NDEFReader?: NdefReaderConstructor }).NDEFReader;
}

/** Whether this browser can read a card held to the phone. */
export function canScan(): boolean {
  return typeof window !== 'undefined' && ndefReader() !== undefined;
}

/**
 * The card's name, and *Scan card*: the phone then waits for a card held to
 * its back and hands over its ID (as the phone writes it, ``04:a1:…``; the
 * server makes it the same as a reader's).
 */
export function ScanCard({
  onScanned,
  busy,
  unsupported,
}: {
  onScanned: (cardId: string, name: string) => void;
  busy: boolean;
  /** Shown where the browser cannot read cards. */
  unsupported: ReactNode;
}) {
  const [name, setName] = useState('');
  const [scanning, setScanning] = useState(false);
  const stop = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      stop.current?.abort();
    },
    [],
  );

  if (!canScan()) return <>{unsupported}</>;

  async function scan() {
    const Reader = ndefReader();
    if (!Reader) return;
    const controller = new AbortController();
    stop.current = controller;
    setScanning(true);
    try {
      const reader = new Reader();
      reader.onreading = (event) => {
        controller.abort();
        setScanning(false);
        if (event.serialNumber) onScanned(event.serialNumber, name);
        else notifyNote('This card has no ID the phone can read.');
      };
      reader.onreadingerror = () => {
        notifyNote('The card could not be read. Hold it still against the back of the phone.');
      };
      await reader.scan({ signal: controller.signal });
    } catch {
      setScanning(false);
      notifyNote('The phone did not allow reading cards. Allow NFC for this page and try again.');
    }
  }

  return (
    <Stack gap="sm">
      <TextInput
        label="Name (optional)"
        placeholder="Student card"
        maxLength={40}
        value={name}
        disabled={scanning}
        onChange={(event) => {
          setName(event.currentTarget.value);
        }}
      />
      {scanning ? (
        <Group gap="md" justify="space-between" className={classes.scanning} role="status">
          <Group gap="sm" wrap="nowrap">
            <IconNfc size={28} stroke={1.5} className={classes.pulse} aria-hidden />
            <Text>Hold the card to the back of your phone.</Text>
          </Group>
          <Button
            variant="default"
            onClick={() => {
              stop.current?.abort();
              setScanning(false);
            }}
          >
            Cancel
          </Button>
        </Group>
      ) : (
        <Group>
          <Button leftSection={<IconNfc size={18} aria-hidden />} loading={busy} onClick={() => void scan()}>
            Scan card
          </Button>
        </Group>
      )}
    </Stack>
  );
}
