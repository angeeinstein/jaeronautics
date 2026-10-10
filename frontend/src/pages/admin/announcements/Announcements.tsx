/**
 * Admin › Announcements (docs/messages-plan.md): writing to the members, and
 * what was sent. Only active, paying members receive anything.
 *
 * - **News** can be switched off by its recipients: they are counted and
 *   left out.
 * - **A notice** reaches everybody it is addressed to.
 * - **The general assembly's invitation** is a notice to all members:
 *   date, time, place and agenda write the text, which can then be changed.
 *   Under two weeks before the date it is held back, unless sending anyway
 *   is ticked (statutes § 10 (3)).
 *
 * Sent through a queue within Settings › Mailings' limits. Data: GET|POST
 * /api/v1/admin/announcements, POST .../count and .../test.
 */
import {
  Alert,
  Checkbox,
  MultiSelect,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Textarea,
  TextInput,
  Button,
} from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router';

import { api, call, type Schemas } from '../../../api/client';
import { DayInput } from '../../../components/DayInput';
import { type Draft, MailingComposer, MailingTable } from '../../../components/Mailing';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { ErrorState, LoadingState } from '../../../components/States';
import { formatDate } from '../../../lib/format';
import { EVERY_30_SECONDS, useLiveRefresh } from '../../../lib/live';

type Audience = Schemas['AudienceIn'];
type Kind = 'news' | 'notice' | 'assembly';

export const announcementsQuery = {
  queryKey: ['admin', 'announcements'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/announcements')),
};

const SCOPES = [
  { value: 'all', label: 'All active members' },
  { value: 'kinds', label: 'Some kinds of member' },
  { value: 'teams', label: 'Members of some teams' },
  { value: 'leads', label: 'All team leads' },
];

const TWO_WEEKS_MS = 14 * 24 * 60 * 60 * 1000;

/** The day and time as the moment it is in the portal's time zone (the browser's). */
export function assemblyMoment(day: string, time: string): Date | null {
  if (!day || !/^\d{1,2}:\d{2}$/.test(time)) return null;
  const moment = new Date(`${day}T${time.padStart(5, '0')}:00`);
  return Number.isNaN(moment.getTime()) ? null : moment;
}

/** The invitation, written from its facts; changed freely afterwards. */
export function invitationText(day: string, time: string, place: string, agenda: string): Draft {
  const items = agenda
    .split('\n')
    .map((line) => line.replace(/^\s*(\d+[.)]|[-*])\s*/, '').trim())
    .filter(Boolean);
  const date = formatDate(day);
  return {
    subject: `Invitation to the general assembly on ${date}`,
    body: [
      'Dear members,',
      '',
      'the board of Joanneum Aeronautics invites you to the general assembly.',
      '',
      `**When:** ${date}, ${time}  `,
      `**Where:** ${place}`,
      '',
      '**Agenda**',
      '',
      ...(items.length ? items.map((item, index) => `${String(index + 1)}. ${item}`) : ['1. …']),
      '',
      'Motions for the general assembly reach the board in writing or by email at least three days before ' +
        'it (statutes § 10 (4)). Ordinary and honorary members vote; another member may vote for you with ' +
        'your written proxy (§ 10 (6)).',
      '',
      'The board',
    ].join('\n'),
  };
}

function useCount(kind: 'news' | 'notice', audience: Audience) {
  return useQuery({
    queryKey: ['admin', 'announcements', 'count', kind, audience],
    queryFn: () => call(api.POST('/api/v1/admin/announcements/count', { body: { kind, audience } })),
    retry: false,
  });
}

function Write({ data }: { data: Schemas['AnnouncementsOut'] }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [kind, setKind] = useState<Kind>('news');
  const [scope, setScope] = useState<Audience['scope']>('all');
  const [kinds, setKinds] = useState<string[]>([]);
  const [teams, setTeams] = useState<string[]>([]);
  const [draft, setDraft] = useState<Draft>({ subject: '', body: '' });
  const [day, setDay] = useState('');
  const [time, setTime] = useState('18:00');
  const [place, setPlace] = useState('');
  const [agenda, setAgenda] = useState('');
  const [lateOk, setLateOk] = useState(false);
  // The moment the page was opened stands for now: minutes do not matter against two weeks.
  const [openedAt] = useState(() => Date.now());

  const assembly = kind === 'assembly';
  const audience: Audience = assembly
    ? { scope: 'all', kinds: [], teams: [] }
    : { scope, kinds: scope === 'kinds' ? kinds : [], teams: scope === 'teams' ? teams.map(Number) : [] };
  const sentKind = kind === 'news' ? 'news' : 'notice';
  const incomplete = (scope === 'kinds' && !kinds.length) || (scope === 'teams' && !teams.length);
  const count = useCount(sentKind, audience);
  const moment = assembly ? assemblyMoment(day, time) : null;
  const late = moment !== null && moment.getTime() - openedAt < TWO_WEEKS_MS;
  const blocked = assembly
    ? !moment
      ? 'Give the date and time.'
      : late && !lateOk
        ? 'Under two weeks before the general assembly.'
        : null
    : incomplete
      ? 'Choose who it is for.'
      : null;

  const before = (
    <Stack gap="md">
      <SegmentedControl
        value={kind}
        onChange={(value) => {
          setKind(value);
        }}
        data={[
          { value: 'news', label: 'News' },
          { value: 'notice', label: 'Notice' },
          { value: 'assembly', label: 'General assembly' },
        ]}
        aria-label="What it is"
        style={{ alignSelf: 'flex-start' }}
      />
      <Text size="sm" c="dimmed">
        {kind === 'news'
          ? 'News can be switched off by whoever receives it; they are left out.'
          : kind === 'notice'
            ? 'A notice reaches everybody it is for, whatever their news setting. Keep it for what members must know.'
            : 'The invitation goes to all active members, at least two weeks ahead, with the agenda (statutes § 10 (3)).'}
      </Text>
      {assembly ? (
        <Stack gap="md">
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
            <DayInput label="Date" required value={day} onChange={setDay} />
            <TextInput
              label="Time"
              type="time"
              required
              value={time}
              onChange={(event) => {
                setTime(event.currentTarget.value);
              }}
            />
            <TextInput
              label="Place"
              placeholder="FH Joanneum, room …"
              value={place}
              onChange={(event) => {
                setPlace(event.currentTarget.value);
              }}
            />
          </SimpleGrid>
          <Textarea
            label="Agenda"
            description="One item per line."
            inputWrapperOrder={['label', 'input', 'description', 'error']}
            autosize
            minRows={4}
            value={agenda}
            onChange={(event) => {
              setAgenda(event.currentTarget.value);
            }}
          />
          <Button
            variant="default"
            disabled={!day}
            onClick={() => {
              setDraft(invitationText(day, time, place, agenda));
            }}
            style={{ alignSelf: 'flex-start' }}
          >
            Write the invitation
          </Button>
          {late ? (
            <Alert color="amber" variant="light">
              <Stack gap="xs">
                <Text size="sm">
                  The general assembly is less than two weeks away. The statutes ask for two weeks&apos;
                  notice.
                </Text>
                <Checkbox
                  label="Send anyway"
                  checked={lateOk}
                  onChange={(event) => {
                    setLateOk(event.currentTarget.checked);
                  }}
                />
              </Stack>
            </Alert>
          ) : null}
        </Stack>
      ) : (
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          <Select
            label="To"
            data={SCOPES}
            value={scope}
            allowDeselect={false}
            onChange={(value) => {
              setScope((value ?? 'all') as Audience['scope']);
            }}
          />
          {scope === 'kinds' ? (
            <MultiSelect
              label="Kinds of member"
              data={data.kinds.map((item) => ({ value: item.value, label: item.label }))}
              value={kinds}
              onChange={setKinds}
            />
          ) : scope === 'teams' ? (
            <MultiSelect
              label="Teams"
              data={data.teams.map((team) => ({ value: String(team.id), label: team.name }))}
              value={teams}
              onChange={setTeams}
            />
          ) : null}
        </SimpleGrid>
      )}
      {count.data?.unsubscribed ? (
        <Text size="sm" c="dimmed">
          {`${String(count.data.unsubscribed)} switched the news off and are left out.`}
        </Text>
      ) : null}
    </Stack>
  );

  return (
    <MailingComposer
      before={before}
      draft={draft}
      onDraft={setDraft}
      recipients={incomplete && !assembly ? 0 : (count.data?.recipients ?? null)}
      blocked={blocked}
      test={(current) =>
        call(api.POST('/api/v1/admin/announcements/test', { body: { kind: sentKind, ...current } }))
      }
      send={(current) =>
        call(
          api.POST('/api/v1/admin/announcements', {
            body: {
              kind: sentKind,
              audience,
              ...current,
              assembly_at: moment ? moment.toISOString() : null,
              late_ok: lateOk,
            },
          }),
        )
      }
      onSent={(mailing) => {
        void client.invalidateQueries({ queryKey: announcementsQuery.queryKey });
        void navigate(`/admin/announcements/${String(mailing.id)}`);
      }}
    />
  );
}

export function Announcements() {
  const list = useQuery(announcementsQuery);
  useLiveRefresh(announcementsQuery.queryKey, EVERY_30_SECONDS);
  return (
    <>
      <PageHeader
        title="Announcements"
        description="Emails to the members. Only active, paying members receive them."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Announcements' }]}
      />
      {list.isPending ? (
        <LoadingState />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : (
        <Stack gap="lg">
          <Panel title="Write">
            <Write data={list.data} />
          </Panel>
          <Panel title="Sent" flush>
            <MailingTable
              items={list.data.items}
              linkTo={(mailing) => `/admin/announcements/${String(mailing.id)}`}
            />
          </Panel>
          <Text size="sm" c="dimmed">
            {`Sent from ${list.data.limits.sender || 'the notifications’ mail account'}, at most ` +
              `${String(list.data.limits.per_hour)} an hour and ${String(list.data.limits.per_day)} a day ` +
              '(Settings › Mailings).'}
          </Text>
        </Stack>
      )}
    </>
  );
}
