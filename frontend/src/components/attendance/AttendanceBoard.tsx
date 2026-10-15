/**
 * Running attendance for a team, or for the association (docs/attendance-plan.md):
 * who is checked in now, hours per person for a period -- this month, the last,
 * the semester, any days -- a person's stays with their corrections (added,
 * changed, deleted; each in the log, the member sees who), a CSV of the period,
 * and the switch. Off, the readers refuse check-ins and the stays are kept.
 *
 * The team's page (pages/teams/manage/Attendance.tsx) and the admins' (pages/
 * admin/Attendance.tsx) draw the same board on their own API: ``AttendanceApi``.
 */
import {
  Button,
  Group,
  Modal,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  UnstyledButton,
} from '@mantine/core';
import { TimePicker } from '@mantine/dates';
import { useMediaQuery } from '@mantine/hooks';
import {
  IconClockHour4,
  IconDoorEnter,
  IconDownload,
  IconPencil,
  IconPlus,
  IconPower,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError, type Schemas } from '../../api/client';
import { formatDate, formatMinutes, formatTime, fromVienna, viennaParts } from '../../lib/format';
import { EVERY_30_SECONDS, useLiveRefresh } from '../../lib/live';
import { notifyDone, notifyFailed } from '../../lib/notify';
import { ConfirmButton } from '../ConfirmButton';
import { DayInput } from '../DayInput';
import { Panel } from '../Panel';
import { Pill } from '../Pill';
import { EmptyState, ErrorState, LoadingState } from '../States';
import { type Stay, StayTable } from './StayTable';

type Overview = Schemas['AttendanceOut'];
type Row = Schemas['HoursRowOut'];

export interface AttendanceApi {
  /** Where its answers are kept: everything under it is fetched again after a change. */
  key: readonly unknown[];
  overview: (start: string, end: string) => Promise<Overview>;
  person: (userId: number, start: string, end: string) => Promise<Schemas['PersonStaysOut']>;
  add: (body: Schemas['StayIn']) => Promise<Stay>;
  change: (stayId: number, body: Schemas['StayChangeIn']) => Promise<Stay>;
  remove: (stayId: number) => Promise<unknown>;
  setEnabled: (enabled: boolean) => Promise<Schemas['SwitchOut']>;
  csvHref: (start: string, end: string) => string;
}

// --- The period -------------------------------------------------------------------------

type Preset = 'month' | 'last' | 'semester' | 'days';

interface Period {
  preset: Preset;
  start: string;
  end: string;
}

function today(): string {
  return viennaParts(new Date().toISOString()).day;
}

function lastDayOf(year: number, month: number): string {
  const day = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

/** The days of a preset: up to today, or the whole of last month. Semesters: October to February, March to September. */
export function periodOf(preset: Exclude<Preset, 'days'>, now = today()): { start: string; end: string } {
  const [year = 0, month = 1] = now.split('-').map(Number);
  if (preset === 'month') return { start: `${now.slice(0, 7)}-01`, end: now };
  if (preset === 'last') {
    const [y, m] = month === 1 ? [year - 1, 12] : [year, month - 1];
    return { start: `${y}-${String(m).padStart(2, '0')}-01`, end: lastDayOf(y, m) };
  }
  if (month >= 3 && month <= 9) return { start: `${year}-03-01`, end: now };
  return { start: `${month >= 10 ? year : year - 1}-10-01`, end: now };
}

const PRESETS = [
  { value: 'month', label: 'This month' },
  { value: 'last', label: 'Last month' },
  { value: 'semester', label: 'This semester' },
  { value: 'days', label: 'Days' },
];

function PeriodPicker({ period, onChange }: { period: Period; onChange: (period: Period) => void }) {
  return (
    <Group gap="sm" align="flex-end">
      <Select
        label="Period"
        w={160}
        data={PRESETS}
        value={period.preset}
        allowDeselect={false}
        onChange={(value) => {
          if (!value || value === 'days') onChange({ ...period, preset: 'days' });
          else onChange({ preset: value as Preset, ...periodOf(value as Exclude<Preset, 'days'>) });
        }}
      />
      <DayInput
        label="From"
        w={140}
        value={period.start}
        onChange={(start) => {
          if (start) onChange({ ...period, preset: 'days', start });
        }}
      />
      <DayInput
        label="To"
        w={140}
        value={period.end}
        onChange={(end) => {
          if (end) onChange({ ...period, preset: 'days', end });
        }}
      />
    </Group>
  );
}

// --- A stay entered or changed by hand ------------------------------------------------------

interface StayFormProps {
  api: AttendanceApi;
  /** The stay changed; none for a new one. */
  stay?: Stay;
  /** Who it is for: fixed for a person's stay, chosen from ``people`` for a new one. */
  userId?: number;
  people: Row[];
  onDone: () => void;
}

function StayForm({ api, stay, userId, people, onDone }: StayFormProps) {
  const client = useQueryClient();
  const started = stay ? viennaParts(stay.started_at) : { day: today(), time: '' };
  const ended = stay?.ended_at ? viennaParts(stay.ended_at) : { day: started.day, time: '' };
  const [who, setWho] = useState<string | null>(userId !== undefined ? String(userId) : null);
  const [inDay, setInDay] = useState(started.day);
  const [inTime, setInTime] = useState(started.time);
  const [outDay, setOutDay] = useState(ended.day);
  const [outTime, setOutTime] = useState(ended.time);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => {
      const times = { started_at: fromVienna(inDay, inTime), ended_at: fromVienna(outDay, outTime) };
      return stay ? api.change(stay.id, times) : api.add({ user_id: Number(who), ...times });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: api.key });
      notifyDone(stay ? 'Stay changed.' : 'Stay added.');
      onDone();
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  // A stay changed keeps its person; a new one needs one chosen.
  const ready = (stay !== undefined || who !== null) && inDay && inTime && outDay && outTime;
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        setErrors({});
        save.mutate();
      }}
    >
      <Stack gap="md">
        {stay || userId !== undefined ? null : (
          <Select
            label="Person"
            required
            searchable
            data={people.map((row) => ({ value: String(row.user_id), label: row.name }))}
            value={who}
            onChange={setWho}
            data-autofocus
          />
        )}
        <SimpleGrid cols={2} spacing="sm">
          <DayInput
            label="Checked in"
            required
            value={inDay}
            onChange={(day) => {
              setInDay(day);
              if (!stay) setOutDay(day);
            }}
          />
          <TimePicker
            label="At"
            required
            format="24h"
            hoursInputLabel="Check-in hours"
            minutesInputLabel="Check-in minutes"
            value={inTime}
            onChange={setInTime}
          />
          <DayInput label="Checked out" required value={outDay} onChange={setOutDay} />
          <TimePicker
            label="At"
            required
            format="24h"
            hoursInputLabel="Check-out hours"
            minutesInputLabel="Check-out minutes"
            value={outTime}
            onChange={setOutTime}
            error={errors.ended_at ?? errors.started_at}
          />
        </SimpleGrid>
        <Text size="xs" c="dimmed">
          The member sees that you corrected it.
        </Text>
        <Button type="submit" loading={save.isPending} disabled={!ready}>
          {stay ? 'Save' : 'Add'}
        </Button>
      </Stack>
    </form>
  );
}

function StayDialog({
  title,
  opened,
  onClose,
  ...form
}: Omit<StayFormProps, 'onDone'> & { title: string; opened: boolean; onClose: () => void }) {
  return (
    <Modal opened={opened} onClose={onClose} title={title}>
      {opened ? <StayForm {...form} onDone={onClose} /> : null}
    </Modal>
  );
}

// --- A person's stays ---------------------------------------------------------------------

function PersonStays({
  api,
  row,
  period,
  people,
}: {
  api: AttendanceApi;
  row: Row;
  period: Period;
  people: Row[];
}) {
  const client = useQueryClient();
  const [changing, setChanging] = useState<Stay | null>(null);
  const [adding, setAdding] = useState(false);
  const stays = useQuery({
    queryKey: [...api.key, 'person', row.user_id, period.start, period.end],
    queryFn: () => api.person(row.user_id, period.start, period.end),
  });
  const remove = useMutation({
    mutationFn: (stay: Stay) => api.remove(stay.id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: api.key });
      notifyDone('Stay deleted.');
    },
    onError: notifyFailed,
  });
  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          {formatDate(period.start)} – {formatDate(period.end)}
        </Text>
        <Button
          size="xs"
          variant="default"
          leftSection={<IconPlus size={16} aria-hidden />}
          onClick={() => {
            setAdding(true);
          }}
        >
          Add a stay
        </Button>
      </Group>
      {stays.isPending ? (
        <LoadingState />
      ) : stays.isError ? (
        <ErrorState error={stays.error} onRetry={() => void stays.refetch()} />
      ) : stays.data.stays.length ? (
        <StayTable
          stays={stays.data.stays}
          label={`Stays of ${row.name}`}
          actions={(stay) => (
            <>
              <Button
                size="xs"
                variant="default"
                leftSection={<IconPencil size={14} aria-hidden />}
                onClick={() => {
                  setChanging(stay);
                }}
              >
                Change
              </Button>
              <ConfirmButton
                size="xs"
                confirmLabel="Yes, delete"
                loading={remove.isPending && remove.variables.id === stay.id}
                onConfirm={() => {
                  remove.mutate(stay);
                }}
              >
                Delete
              </ConfirmButton>
            </>
          )}
        />
      ) : (
        <EmptyState>No stays in this period.</EmptyState>
      )}
      <StayDialog
        title={`Change a stay of ${row.name}`}
        opened={changing !== null}
        onClose={() => {
          setChanging(null);
        }}
        api={api}
        stay={changing ?? undefined}
        people={people}
      />
      <StayDialog
        title={`Add a stay for ${row.name}`}
        opened={adding}
        onClose={() => {
          setAdding(false);
        }}
        api={api}
        userId={row.user_id}
        people={people}
      />
    </Stack>
  );
}

// --- The board ------------------------------------------------------------------------------

function HoursTable({ rows, onOpen }: { rows: Row[]; onOpen: (row: Row) => void }) {
  return (
    <Table.ScrollContainer minWidth={480} type="native">
      <Table verticalSpacing="sm" highlightOnHover aria-label="Hours per person">
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Name</Table.Th>
            <Table.Th scope="col" ta="right">
              Time
            </Table.Th>
            <Table.Th scope="col" ta="right">
              Stays
            </Table.Th>
            <Table.Th scope="col">Not checked out</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row) => (
            <Table.Tr
              key={row.user_id}
              style={{ cursor: 'pointer' }}
              onClick={() => {
                onOpen(row);
              }}
            >
              <Table.Td>
                <UnstyledButton
                  fw={600}
                  fz="sm"
                  onClick={(event) => {
                    event.stopPropagation();
                    onOpen(row);
                  }}
                >
                  {row.name}
                </UnstyledButton>
              </Table.Td>
              <Table.Td ta="right" className="ja-figures" style={{ whiteSpace: 'nowrap' }}>
                {formatMinutes(row.minutes)}
              </Table.Td>
              <Table.Td ta="right" className="ja-figures">
                {row.stays}
              </Table.Td>
              <Table.Td>
                {row.not_checked_out ? <Pill tone="pending">{String(row.not_checked_out)}</Pill> : null}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function HereNow({ present }: { present: Overview['present'] }) {
  return (
    <Panel icon={IconDoorEnter} title="Here now">
      {present.length ? (
        <Stack gap={6}>
          {present.map((stay) => (
            <Group key={stay.stay_id} justify="space-between" gap="sm">
              <Text fw={600} size="sm">
                {stay.name}
              </Text>
              <Text size="sm" c="dimmed">
                since <span className="ja-figures">{formatTime(stay.since)}</span>
                {stay.place ? `, ${stay.place}` : ''}
              </Text>
            </Group>
          ))}
        </Stack>
      ) : (
        <EmptyState>Nobody checked in.</EmptyState>
      )}
    </Panel>
  );
}

export function SwitchPanel({
  api,
  enabled,
  where,
}: {
  api: AttendanceApi;
  enabled: boolean;
  where: string;
}) {
  const client = useQueryClient();
  const change = useMutation({
    mutationFn: (on: boolean) => api.setEnabled(on),
    onSuccess: (data) => {
      void client.invalidateQueries({ queryKey: api.key });
      // The side menus follow the switch.
      void client.invalidateQueries({ queryKey: ['teams'] });
      void client.invalidateQueries({ queryKey: ['me'] });
      notifyDone(data.enabled ? 'Attendance is on.' : 'Attendance is off.');
    },
    onError: notifyFailed,
  });
  return (
    <Panel icon={IconPower} title="Attendance">
      <Stack gap="xs">
        <Switch
          label="Attendance on"
          checked={enabled}
          disabled={change.isPending}
          onChange={(event) => {
            change.mutate(event.currentTarget.checked);
          }}
        />
        <Text size="sm" c="dimmed">
          {enabled
            ? `Members of ${where} check in and out at its attendance readers.`
            : `Off: the readers refuse check-ins. Stays already recorded are kept.`}
        </Text>
      </Stack>
    </Panel>
  );
}

/** The switch alone, where settings are: Admin › Settings › General for the association. */
export function AttendanceSwitch({ api, where }: { api: AttendanceApi; where: string }) {
  const { start, end } = periodOf('month');
  const overview = useQuery({
    queryKey: [...api.key, 'overview', start, end],
    queryFn: () => api.overview(start, end),
  });
  if (!overview.data) return null;
  return <SwitchPanel api={api} enabled={overview.data.enabled} where={where} />;
}

export function AttendanceBoard({ api, where }: { api: AttendanceApi; where: string }) {
  const [period, setPeriod] = useState<Period>({ preset: 'month', ...periodOf('month') });
  const [person, setPerson] = useState<Row | null>(null);
  const [adding, setAdding] = useState(false);
  const phone = useMediaQuery('(max-width: 48em)');
  const key = [...api.key, 'overview', period.start, period.end];
  const overview = useQuery({ queryKey: key, queryFn: () => api.overview(period.start, period.end) });
  // Check-ins and check-outs at the readers show without reloading.
  useLiveRefresh(key, EVERY_30_SECONDS);

  if (overview.isPending) return <LoadingState />;
  if (overview.isError) return <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />;
  const { enabled, rows, present } = overview.data;
  const recorded = rows.some((row) => row.stays > 0);
  const switchPanel = <SwitchPanel api={api} enabled={enabled} where={where} />;
  return (
    <Stack gap="lg">
      {enabled ? null : switchPanel}
      {enabled ? <HereNow present={present} /> : null}
      {enabled || recorded ? (
        <Panel
          icon={IconClockHour4}
          title="Hours"
          flush
          actions={
            <>
              <Button
                size="xs"
                variant="default"
                leftSection={<IconPlus size={16} aria-hidden />}
                onClick={() => {
                  setAdding(true);
                }}
              >
                Add a stay
              </Button>
              <Button
                size="xs"
                variant="default"
                component="a"
                href={api.csvHref(overview.data.start, overview.data.end)}
                leftSection={<IconDownload size={16} aria-hidden />}
              >
                CSV
              </Button>
            </>
          }
        >
          <Stack gap="sm" p="md" pb={rows.length ? 0 : 'md'}>
            <PeriodPicker period={period} onChange={setPeriod} />
            <Text size="sm" c="dimmed">
              <span className="ja-figures">
                {formatMinutes(rows.reduce((sum, row) => sum + row.minutes, 0))}
              </span>{' '}
              altogether, in full minutes. A stay not checked out counts nothing until it is corrected.
            </Text>
            {rows.length ? null : <EmptyState>Nobody here yet.</EmptyState>}
          </Stack>
          {rows.length ? <HoursTable rows={rows} onOpen={setPerson} /> : null}
        </Panel>
      ) : null}
      {enabled ? switchPanel : null}
      <Modal
        opened={person !== null}
        onClose={() => {
          setPerson(null);
        }}
        title={person?.name}
        size="xl"
        fullScreen={phone}
      >
        {person ? <PersonStays api={api} row={person} period={period} people={rows} /> : null}
      </Modal>
      <StayDialog
        title="Add a stay"
        opened={adding}
        onClose={() => {
          setAdding(false);
        }}
        api={api}
        people={rows}
      />
    </Stack>
  );
}
