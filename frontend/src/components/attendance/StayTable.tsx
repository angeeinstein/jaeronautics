/**
 * Stays at the attendance readers (docs/attendance-plan.md), as a member sees
 * their own on My Account › Attendance and a team a person's: the day, in and
 * out with where it was tapped, the time counted in full minutes, and what is
 * worth knowing -- checked in now, not checked out (counts nothing until
 * corrected), corrected by whom.
 */
import { Group, Stack, Table, Text, VisuallyHidden } from '@mantine/core';
import type { ReactNode } from 'react';

import type { Schemas } from '../../api/client';
import { formatDateTime, formatDayOf, formatMinutes, formatTime } from '../../lib/format';
import { Pill } from '../Pill';

export type Stay = Schemas['StayOut'];

/** "Not checked out", "Checked in now", or who corrected it. */
export function StayNote({ stay }: { stay: Stay }) {
  if (stay.state === 'open') return <Pill tone="info">Checked in now</Pill>;
  if (stay.state === 'not_checked_out') return <Pill tone="pending">Not checked out</Pill>;
  if (stay.corrected_by)
    return (
      <Text size="sm" c="dimmed">
        Corrected by {stay.corrected_by}
      </Text>
    );
  if (stay.corrected_at)
    return (
      <Text size="sm" c="dimmed">
        Corrected
      </Text>
    );
  return null;
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
}: {
  stays: Stay[];
  label?: string;
  actions?: (stay: Stay) => ReactNode;
}) {
  return (
    <Table.ScrollContainer minWidth={actions ? 640 : 520} type="native">
      <Table verticalSpacing="sm" aria-label={label}>
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
          {stays.map((stay) => (
            <Table.Tr key={stay.id}>
              <Table.Td className="ja-figures" style={{ whiteSpace: 'nowrap' }}>
                {formatDayOf(stay.started_at)}
              </Table.Td>
              <Table.Td>
                <Moment iso={stay.started_at} sameDayAs={stay.started_at} place={stay.in_place} />
              </Table.Td>
              <Table.Td>
                {stay.ended_at ? (
                  <Moment iso={stay.ended_at} sameDayAs={stay.started_at} place={stay.out_place} />
                ) : (
                  <Text size="sm" c="dimmed">
                    –
                  </Text>
                )}
              </Table.Td>
              <Table.Td ta="right" className="ja-figures" style={{ whiteSpace: 'nowrap' }}>
                {stay.minutes !== null ? formatMinutes(stay.minutes) : '–'}
              </Table.Td>
              <Table.Td>
                <StayNote stay={stay} />
              </Table.Td>
              {actions ? (
                <Table.Td>
                  <Group gap="xs" justify="flex-end" wrap="nowrap">
                    {actions(stay)}
                  </Group>
                </Table.Td>
              ) : null}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

/** The minutes of the stays that count. */
export function totalMinutes(stays: Stay[]): number {
  return stays.reduce((sum, stay) => sum + (stay.minutes ?? 0), 0);
}
