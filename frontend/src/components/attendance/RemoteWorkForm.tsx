/**
 * Adding remote work, or changing one's own (docs/attendance-plan.md, "Remote
 * work"): for whom it counts -- a team, or the association -- the day, from,
 * to, and what was done. It counts at once; nobody confirms it. Opened from the
 * team's page and from My Account › Attendance.
 *
 * Data: POST /api/v1/account/attendance/<owner>/remote, PUT and DELETE
 * /api/v1/account/attendance/remote/<id> (aeronautics_members/api/attendance.py).
 */
import { Button, Group, Modal, Select, SimpleGrid, Stack, Text, Textarea } from '@mantine/core';
import { TimePicker } from '@mantine/dates';
import { useMediaQuery } from '@mantine/hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, ApiError, call } from '../../api/client';
import { attendanceDay, formatDate, formatMinutes, viennaParts } from '../../lib/format';
import { notifyDone, notifyFailed } from '../../lib/notify';
import type { Stay } from './StayTable';

/** Where the member's own attendance is kept: everything under it is fetched again after a change. */
export const ACCOUNT_ATTENDANCE_KEY = ['account', 'attendance'] as const;

const NOTE_MAX = 200;
const WEEKDAY = new Intl.DateTimeFormat('en-GB', { weekday: 'short', timeZone: 'UTC' });

function addDays(day: string, days: number): string {
  const [year = 0, month = 1, date = 1] = day.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, date + days)).toISOString().slice(0, 10);
}

/** The days remote work may be added for, today first: "Today, Sat 21.11.2026". */
export function remoteDays(from: string, today: string): { value: string; label: string }[] {
  const days = [];
  for (let day = today, back = 0; day >= from; day = addDays(day, -1), back++) {
    const weekday = WEEKDAY.format(new Date(`${day}T12:00:00Z`));
    const prefix = back === 0 ? 'Today, ' : back === 1 ? 'Yesterday, ' : '';
    days.push({ value: day, label: `${prefix}${weekday} ${formatDate(day)}` });
  }
  return days;
}

function minutesOf(time: string): number {
  const [hour = 0, minute = 0] = time.split(':').map(Number);
  return hour * 60 + minute;
}

const DIVIDE = 3 * 60 + 15;

/** How long from ``start`` to ``end`` is on one attendance day: a time before 03:15 is the night after. */
export function remoteMinutesOf(start: string, end: string): number {
  if (!start || !end) return 0;
  const from = minutesOf(start) + (minutesOf(start) < DIVIDE ? 1440 : 0);
  const to = minutesOf(end) + (minutesOf(end) <= DIVIDE ? 1440 : 0);
  return Math.max(0, to - from);
}

interface Props {
  /** Whom it counts for: "association", or the team's slug, with its name. */
  owner: string;
  ownerName: string;
  remoteFrom: string;
  today: string;
  /** The entry changed; none for a new one. */
  stay?: Stay;
  onDone: () => void;
}

function RemoteWorkFormBody({ owner, ownerName, remoteFrom, today, stay, onDone }: Props) {
  const client = useQueryClient();
  const days = remoteDays(remoteFrom, today);
  const [day, setDay] = useState<string | null>(
    stay ? attendanceDay(stay.started_at) : (days[1]?.value ?? today),
  );
  const [start, setStart] = useState(stay ? viennaParts(stay.started_at).time : '');
  const [end, setEnd] = useState(stay?.ended_at ? viennaParts(stay.ended_at).time : '');
  const [note, setNote] = useState(stay?.note ?? '');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => {
      const body = { day: day ?? today, start, end, note };
      return stay
        ? call(
            api.PUT('/api/v1/account/attendance/remote/{stay_id}', {
              params: { path: { stay_id: stay.id } },
              body,
            }),
          )
        : call(api.POST('/api/v1/account/attendance/{owner}/remote', { params: { path: { owner } }, body }));
    },
    onSuccess: (saved) => {
      void client.invalidateQueries({ queryKey: ACCOUNT_ATTENDANCE_KEY });
      notifyDone(stay ? 'Saved.' : `Added ${formatMinutes(saved.minutes ?? 0)}.`);
      onDone();
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const length = remoteMinutesOf(start, end);
  const ready = Boolean(day && start && end && note.trim());
  const away = owner === 'association' ? 'away from its readers' : 'away from the lab';
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        setErrors({});
        save.mutate();
      }}
    >
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Worked on something for {ownerName} {away}, like CAD at home? Add it, and it counts with your time.
        </Text>
        <Select
          label="Day"
          data={days}
          value={day}
          allowDeselect={false}
          onChange={setDay}
          description={`Today or one of the ${days.length - 1} days before.`}
          error={errors.day}
        />
        <SimpleGrid cols={2} spacing="sm">
          <TimePicker
            label="From"
            required
            format="24h"
            hoursInputLabel="From, hours"
            minutesInputLabel="From, minutes"
            value={start}
            onChange={setStart}
            error={errors.start}
          />
          <TimePicker
            label="To"
            required
            format="24h"
            hoursInputLabel="To, hours"
            minutesInputLabel="To, minutes"
            value={end}
            onChange={setEnd}
            error={errors.end}
          />
        </SimpleGrid>
        <Textarea
          label="What did you do?"
          required
          autosize
          minRows={2}
          maxLength={NOTE_MAX}
          placeholder="Wing rib CAD, revision 3"
          description="A few words are enough. Whoever runs the attendance sees them with the entry."
          value={note}
          onChange={(event) => {
            setNote(event.currentTarget.value);
          }}
          error={errors.note}
          data-autofocus
        />
        <Group justify="space-between" align="center" gap="sm">
          <Text size="sm" c="dimmed">
            {length ? (
              <>
                <span className="ja-figures" style={{ color: 'var(--ja-text)' }}>
                  {formatMinutes(length)}
                </span>
                , added right away.
              </>
            ) : null}
          </Text>
          <Button type="submit" loading={save.isPending} disabled={!ready}>
            {stay ? 'Save' : 'Add'}
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

/** The form in a dialog, titled with whom it counts for. */
export function RemoteWorkDialog({
  opened,
  onClose,
  ...props
}: Omit<Props, 'onDone'> & {
  opened: boolean;
  onClose: () => void;
}) {
  const phone = useMediaQuery('(max-width: 48em)');
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      fullScreen={phone}
      title={`${props.stay ? 'Change remote work' : 'Add remote work'} for ${props.ownerName}`}
    >
      {opened ? <RemoteWorkFormBody {...props} onDone={onClose} /> : null}
    </Modal>
  );
}
