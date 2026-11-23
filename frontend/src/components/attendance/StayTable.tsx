/**
 * Stays (docs/attendance-plan.md), as a member sees their own on My Account ›
 * Attendance and a team a person's: the day, in and out with where it was
 * tapped, the time counted in full minutes, and what is worth knowing --
 * checked in now, not checked out (counts nothing until corrected), corrected
 * by whom. Remote work, added by the member, shows *Remote* and what they did;
 * one marked as not counting is crossed out, with who marked it and why.
 *
 * On a phone each stay is a small block instead of a row (StayTable.module.css),
 * and a long list shows its newest stays with *Show all* for the rest.
 */
import { Button, Group, Stack, Table, Text, VisuallyHidden } from '@mantine/core';
import { type ReactNode, useState } from 'react';

import type { Schemas } from '../../api/client';
import { formatDateTime, formatDayOf, formatMinutes, formatTime } from '../../lib/format';
import { Pill } from '../Pill';
import classes from './StayTable.module.css';

export type Stay = Schemas['StayOut'];

/** How many stays a long list shows before *Show all*. */
export const SHOWN_AT_FIRST = 15;

/** What is worth knowing about a stay: remote work and what was done, the state, who corrected or marked it. */
export function StayNote({ stay }: { stay: Stay }) {
  const parts: ReactNode[] = [];
  if (stay.remote) {
    parts.push(
      <Group key="remote" gap={8} wrap="nowrap" align="flex-start">
        <Pill tone="info">Remote</Pill>
        {stay.note ? (
          <Text size="sm" className={stay.not_counted_at ? classes.crossed : undefined}>
            {stay.note}
          </Text>
        ) : null}
      </Group>,
    );
  }
  if (stay.not_counted_at) {
    parts.push(
      <Group key="marked" gap={8} wrap="wrap" align="baseline">
        <Pill tone="failed">Not counted</Pill>
        <Text size="sm" c="dimmed">
          {stay.not_counted_by ? `by ${stay.not_counted_by}` : ''}
          {stay.not_counted_reason ? `${stay.not_counted_by ? ': ' : ''}${stay.not_counted_reason}` : ''}
        </Text>
      </Group>,
    );
  } else if (stay.state === 'open') {
    parts.push(
      <Pill key="state" tone="info">
        Checked in now
      </Pill>,
    );
  } else if (stay.state === 'not_checked_out') {
    parts.push(
      <Pill key="state" tone="pending">
        Not checked out
      </Pill>,
    );
  } else if (stay.corrected_by) {
    parts.push(
      <Text key="by" size="sm" c="dimmed">
        Corrected by {stay.corrected_by}
      </Text>,
    );
  } else if (stay.corrected_at) {
    parts.push(
      <Text key="by" size="sm" c="dimmed">
        Corrected
      </Text>,
    );
  }
  if (!parts.length) return null;
  return <Stack gap={4}>{parts}</Stack>;
}

function Moment({ iso, sameDayAs, place }: { iso: string; sameDayAs: string; place: string | null }) {
  return (
    <Stack gap={0}>
      <Text size="sm" className="ja-figures" style={{ whiteSpace: 'nowrap' }}>
        {formatDayOf(iso) === formatDayOf(sameDayAs) ? formatTime(iso) : formatDateTime(iso)}
      </Text>
      {place ? (
        <Text size="xs" c="dimmed">
          {place}
        </Text>
      ) : null}
    </Stack>
  );
}

export function StayTable({
  stays,
  label = 'Stays',
  actions,
  limit = SHOWN_AT_FIRST,
}: {
  stays: Stay[];
  label?: string;
  actions?: (stay: Stay) => ReactNode;
  /** Shown before *Show all*; the newest first, as they come. */
  limit?: number;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? stays : stays.slice(0, limit);
  return (
    <>
      <Table.ScrollContainer minWidth={0} type="native">
        <Table verticalSpacing="sm" aria-label={label} className={classes.table}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Day</Table.Th>
              <Table.Th scope="col">In</Table.Th>
              <Table.Th scope="col">Out</Table.Th>
              <Table.Th scope="col" ta="right">
                Time
              </Table.Th>
              <Table.Th scope="col">
                <VisuallyHidden>Note</VisuallyHidden>
              </Table.Th>
              {actions ? (
                <Table.Th scope="col">
                  <VisuallyHidden>Actions</VisuallyHidden>
                </Table.Th>
              ) : null}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {shown.map((stay) => {
              const buttons = actions ? actions(stay) : null;
              return (
                <Table.Tr key={stay.id}>
                  <Table.Td className={`ja-figures ${classes.day}`} style={{ whiteSpace: 'nowrap' }}>
                    {formatDayOf(stay.started_at)}
                  </Table.Td>
                  <Table.Td className={classes.in}>
                    <Moment iso={stay.started_at} sameDayAs={stay.started_at} place={stay.in_place} />
                  </Table.Td>
                  <Table.Td className={classes.out}>
                    {stay.ended_at ? (
                      <Moment iso={stay.ended_at} sameDayAs={stay.started_at} place={stay.out_place} />
                    ) : (
                      <Text size="sm" c="dimmed">
                        –
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td
                    ta="right"
                    className={`ja-figures ${classes.time} ${stay.not_counted_at ? classes.crossed : ''}`}
                    style={{ whiteSpace: 'nowrap' }}
                  >
                    {stay.minutes !== null ? formatMinutes(stay.minutes) : '–'}
                  </Table.Td>
                  <Table.Td className={classes.note}>
                    <StayNote stay={stay} />
                  </Table.Td>
                  {actions ? (
                    <Table.Td className={classes.actions}>
                      {buttons ? (
                        <Group gap="xs" justify="flex-end" wrap="nowrap">
                          {buttons}
                        </Group>
                      ) : null}
                    </Table.Td>
                  ) : null}
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {stays.length > shown.length ? (
        <Group px="md" py="sm" style={{ borderTop: '1px solid var(--ja-border)' }}>
          <Button
            size="xs"
            variant="default"
            onClick={() => {
              setAll(true);
            }}
          >
            Show all {stays.length}
          </Button>
        </Group>
      ) : null}
    </>
  );
}

/** What a stay counts: nothing while open, not checked out, or marked as not counting. */
export function countedMinutes(stay: Stay): number {
  return stay.not_counted_at ? 0 : (stay.minutes ?? 0);
}

/** The minutes of the stays that count. */
export function totalMinutes(stays: Stay[]): number {
  return stays.reduce((sum, stay) => sum + countedMinutes(stay), 0);
}

/** Of those, the remote work's. */
export function remoteMinutes(stays: Stay[]): number {
  return stays.reduce((sum, stay) => sum + (stay.remote ? countedMinutes(stay) : 0), 0);
}
