/**
 * My Account › Attendance (docs/attendance-plan.md): one's stays at the
 * attendance readers, per team (and the association), a month at a time, with
 * the time counted in full minutes. A stay not checked out counts nothing
 * until the team corrects it: *Ask for a correction* opens *Message the
 * leads* filled in with the stay -- the contact form for the association's
 * readers. Shown in the side menu while attendance is on for somebody; once
 * it is not, still reached from *Privacy and data* while they have stays.
 *
 * Where a team (or the association) allows it, the member adds remote work in
 * its panel (*Add remote work*), and changes or deletes their own while it is
 * in the last days and nobody has marked it as not counting. The month's time
 * says how much of it was remote, quietly (docs/attendance-plan.md, "Remote
 * work").
 *
 * Data: GET /api/v1/account/attendance (aeronautics_members/api/attendance.py).
 */
import { Button, Group, Select, Stack, Text } from '@mantine/core';
import { IconClockHour4, IconMessage, IconPencil, IconPlus } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call, type Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { ACCOUNT_ATTENDANCE_KEY, RemoteWorkDialog } from '../../components/attendance/RemoteWorkForm';
import { remoteMinutes, type Stay, StayTable, totalMinutes } from '../../components/attendance/StayTable';
import { ConfirmButton } from '../../components/ConfirmButton';
import { notifyDone, notifyFailed } from '../../lib/notify';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import { attendanceDay, formatDayOf, formatMinutes, formatTime } from '../../lib/format';
import { EVERY_30_SECONDS, useLiveRefresh } from '../../lib/live';
import { MessageLeads } from '../teams/MessageLeads';

type Owner = Schemas['OwnerStaysOut'];

const attendanceKey = ACCOUNT_ATTENDANCE_KEY;

const monthName = new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' });

/** "2026-10" of a stay: of its attendance day, so a night after the 31st stays in its month. */
function monthOf(stay: Stay): string {
  return attendanceDay(stay.started_at).slice(0, 7);
}

function monthLabel(month: string): string {
  return monthName.format(new Date(`${month}-01T00:00:00Z`));
}

function thisMonth(): string {
  return attendanceDay(new Date().toISOString()).slice(0, 7);
}

/** What the request says: the stay, and room for when they left. */
function correctionText(stay: Stay) {
  const where = stay.in_place ? ` (${stay.in_place})` : '';
  return {
    subject: `Attendance on ${formatDayOf(stay.started_at)}`,
    message: `Please correct my stay on ${formatDayOf(stay.started_at)}: checked in at ${formatTime(stay.started_at)}${where}, not checked out. I left at `,
  };
}

function AskForCorrection({ owner, stay }: { owner: Owner; stay: Stay }) {
  const filledIn = correctionText(stay);
  if (owner.ask === 'team')
    return (
      <MessageLeads
        slug={owner.owner}
        teamName={owner.name}
        label="Ask for a correction"
        size="xs"
        filledIn={filledIn}
      />
    );
  const query = new URLSearchParams({ topic: 'other', ...filledIn });
  return (
    <Button
      component={AppLink}
      to={`/contact?${query.toString()}`}
      size="xs"
      variant="default"
      leftSection={<IconMessage size={16} aria-hidden />}
    >
      Ask for a correction
    </Button>
  );
}

/** Change or delete one's own remote work, while it may be. */
function OwnRemote({ stay, onChange }: { stay: Stay; onChange: (stay: Stay) => void }) {
  const client = useQueryClient();
  const remove = useMutation({
    mutationFn: () =>
      call(
        api.DELETE('/api/v1/account/attendance/remote/{stay_id}', { params: { path: { stay_id: stay.id } } }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: attendanceKey });
      notifyDone('Deleted.');
    },
    onError: notifyFailed,
  });
  return (
    <>
      <Button
        size="xs"
        variant="default"
        leftSection={<IconPencil size={14} aria-hidden />}
        onClick={() => {
          onChange(stay);
        }}
      >
        Change
      </Button>
      <ConfirmButton
        size="xs"
        confirmLabel="Yes, delete"
        loading={remove.isPending}
        onConfirm={() => {
          remove.mutate();
        }}
      >
        Delete
      </ConfirmButton>
    </>
  );
}

/** The month's time, and how much of it was remote -- quietly, where remote work is added or was. */
function MonthTime({ stays, label, remote }: { stays: Stay[]; label: string; remote: boolean }) {
  const away = remoteMinutes(stays);
  const all = totalMinutes(stays);
  return (
    <Stack gap={0} align="flex-end">
      <Text className="ja-figures" fw={600} size="lg">
        {formatMinutes(all)}
      </Text>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      {remote || away ? (
        <Text size="xs" c="dimmed" className="ja-figures">
          {formatMinutes(all - away)} at the readers · {formatMinutes(away)} remote
        </Text>
      ) : null}
    </Stack>
  );
}

function OwnerPanel({ owner, remoteFrom, today }: { owner: Owner; remoteFrom: string; today: string }) {
  const months = [...new Set([thisMonth(), ...owner.stays.map(monthOf)])].sort().reverse();
  const [month, setMonth] = useState(
    months.find((item) => owner.stays.some((s) => monthOf(s) === item)) ?? thisMonth(),
  );
  const [adding, setAdding] = useState(false);
  const [changing, setChanging] = useState<Stay | null>(null);
  const shown = owner.stays.filter((stay) => monthOf(stay) === month);
  const remoteHere = owner.remote === true || owner.stays.some((stay) => stay.remote);
  return (
    <Panel
      icon={IconClockHour4}
      title={owner.name}
      flush
      actions={
        owner.remote ? (
          <Button
            size="xs"
            variant="default"
            leftSection={<IconPlus size={14} aria-hidden />}
            onClick={() => {
              setAdding(true);
            }}
          >
            Add remote work
          </Button>
        ) : undefined
      }
    >
      <Stack gap="sm" p="md" pb={shown.length ? 0 : 'md'}>
        {owner.current ? null : (
          <Text size="sm" c="dimmed">
            Kept for you: attendance is off here, or you are no longer a member.
          </Text>
        )}
        <Group justify="space-between" align="flex-end" gap="sm">
          <Select
            aria-label="Month"
            size="sm"
            w={200}
            data={months.map((item) => ({ value: item, label: monthLabel(item) }))}
            value={month}
            allowDeselect={false}
            onChange={(value) => {
              if (value) setMonth(value);
            }}
          />
          <Group gap="lg" align="flex-start">
            <MonthTime stays={shown} label={monthLabel(month)} remote={remoteHere} />
            <Stack gap={0} align="flex-end">
              <Text className="ja-figures" fw={600} size="lg">
                {formatMinutes(totalMinutes(owner.stays))}
              </Text>
              <Text size="xs" c="dimmed">
                Altogether
              </Text>
            </Stack>
          </Group>
        </Group>
        {shown.length ? null : <EmptyState>Nothing recorded this month.</EmptyState>}
      </Stack>
      {shown.length ? (
        <StayTable
          key={month}
          stays={shown}
          label={`Your time with ${owner.name}`}
          actions={(stay) =>
            stay.state === 'not_checked_out' ? (
              <AskForCorrection owner={owner} stay={stay} />
            ) : stay.may_change ? (
              <OwnRemote stay={stay} onChange={setChanging} />
            ) : null
          }
        />
      ) : null}
      <RemoteWorkDialog
        opened={adding || changing !== null}
        onClose={() => {
          setAdding(false);
          setChanging(null);
        }}
        owner={owner.owner}
        ownerName={owner.name}
        remoteFrom={remoteFrom}
        today={today}
        stay={changing ?? undefined}
      />
    </Panel>
  );
}

export function Attendance() {
  const attendance = useQuery({
    queryKey: attendanceKey,
    queryFn: () => call(api.GET('/api/v1/account/attendance')),
  });
  // A check-in or check-out at a reader shows without reloading.
  useLiveRefresh(attendanceKey, EVERY_30_SECONDS);
  const remote = attendance.data?.owners.some(
    (owner) => owner.remote === true || owner.stays.some((s) => s.remote),
  );
  return (
    <>
      <PageHeader
        title="Attendance"
        description={
          remote
            ? 'Your time at the readers and the remote work you added, counted in full minutes.'
            : 'Your time at the readers, counted in full minutes.'
        }
        crumbs={[{ label: 'My Account', to: '/account' }, { label: 'Attendance' }]}
      />
      {attendance.isPending ? (
        <LoadingState />
      ) : attendance.isError ? (
        <ErrorState error={attendance.error} onRetry={() => void attendance.refetch()} />
      ) : attendance.data.owners.length ? (
        <Stack gap="lg">
          {/* Where they check in now first; what is only kept after. */}
          {[...attendance.data.owners]
            .sort((a, b) => Number(b.current) - Number(a.current))
            .map((owner) => (
              <OwnerPanel
                key={owner.owner}
                owner={owner}
                remoteFrom={attendance.data.remote_from}
                today={attendance.data.today}
              />
            ))}
          <Text size="sm" c="dimmed">
            A stay not checked out counts nothing until it is corrected.
            {remote ? ' Remote work counts right away; the team can mark an entry as not counted.' : ''}
          </Text>
        </Stack>
      ) : (
        <EmptyState>Nothing recorded.</EmptyState>
      )}
    </>
  );
}
