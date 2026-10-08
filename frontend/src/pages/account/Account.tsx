/**
 * My Account › Overview (docs/frontend-structure.md, 6): who this is -- the
 * picture, the name, the kind of membership -- what is to be done first, then
 * a tile for each part, the whole of it the way to that part's page: the
 * membership with how far the paid year has run, the forum, one's teams. The
 * details are on the pages in the side menu. An account without a membership
 * -- one used only to run the portal -- has its address and the way to start
 * one.
 *
 * Data: GET /api/v1/account (aeronautics_members/api/account.py), kept
 * current by useAccount.
 */
import { Avatar, Button, Group, Stack, Text } from '@mantine/core';
import { type ReactNode, useId } from 'react';

import { can, displayName, initials, useMe } from '../../api/session';
import { AppLink } from '../../app/AppLink';
import { useDocumentTitle } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { Pill, type Tone } from '../../components/Pill';
import { ErrorState, LoadingState } from '../../components/States';
import { TeamMark } from '../../components/TeamMark';
import { formatDate } from '../../lib/format';
import classes from './Account.module.css';
import { EmailsCard, ResendButton } from './Emails';
import { forumUsername } from './ForumCard';
import { type Account as AccountData, type MemberAccount, useAccount } from './shared';

/** For an account without one: the way to start it -- here, and on the pages about a membership. */
export function NoMembership() {
  const me = useMe();
  return (
    <Panel title="Membership">
      <Stack gap="sm">
        <Text>This account has no membership.</Text>
        <Text size="sm" c="dimmed">
          {can(me.data, 'admin.access')
            ? 'An account used only to run the portal does not need one. If you are also a member of the association, you can start your membership with this login.'
            : 'You can start your membership with this login.'}
        </Text>
        <Group>
          <Button component={AppLink} to="/account/create-membership">
            Become a member
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

function Hero({ account }: { account: AccountData }) {
  const me = useMe().data;
  const member = account.member;
  const identity = member?.identity;
  const line = [identity?.member_category_label, identity?.year_group, account.email.address].filter(Boolean);
  return (
    <section className={classes.hero} aria-labelledby="account-name">
      <Avatar src={me?.picture_url} alt="" color="brand" variant="filled" size={96} radius="xl" fz={32}>
        {me ? initials(me) : null}
      </Avatar>
      <div className={classes.who}>
        <h1 className={classes.name} id="account-name">
          {me ? displayName(me) : account.email.address}
        </h1>
        <span className={classes.line}>{line.join(' · ')}</span>
      </div>
    </section>
  );
}

/** What is to be done first: the addresses waiting to be confirmed, each link sent again from here. */
function ToDo({ account }: { account: AccountData }) {
  const note = account.to_confirm;
  if (!note) return null;
  const waiting: { which: 'private' | 'work'; label: string }[] = [];
  if (!account.email.confirmed) waiting.push({ which: 'private', label: 'Send to your private email' });
  if (account.member?.work_email && !account.member.work_email.confirmed)
    waiting.push({ which: 'work', label: 'Send to your university email' });
  return (
    <section className={classes.todo} aria-label="To do">
      <Text size="sm">{note.text}</Text>
      <Group gap="xs">
        {waiting.map((address) => (
          <ResendButton key={address.which} which={address.which} size="xs">
            {waiting.length === 1 ? 'Send the link again' : address.label}
          </ResendButton>
        ))}
      </Group>
    </section>
  );
}

/** A tile: named by its title, described by everything else on it. */
function Tile({
  to,
  title,
  badge,
  wide = false,
  children,
}: {
  to: string;
  title: string;
  badge?: ReactNode;
  /** Across the whole row: a list, which would otherwise stand alone beside a gap. */
  wide?: boolean;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <AppLink
      to={to}
      className={classes.tile}
      data-wide={wide || undefined}
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-badge ${id}-body`}
    >
      <span className={classes.head}>
        <span className={classes.title} id={`${id}-title`}>
          {title}
        </span>
        {badge ? <span id={`${id}-badge`}>{badge}</span> : null}
      </span>
      <span id={`${id}-body`} style={{ display: 'contents' }}>
        {children}
      </span>
    </AppLink>
  );
}

const DAY = 24 * 60 * 60 * 1000;

/**
 * How much of the paid time has run, from its start -- the membership's first
 * day, or the first of January of the year paid for, whichever is later -- to
 * the day it is paid until.
 */
export function passed(startsOn: string | null, endsOn: string, today = new Date()): number {
  const end = new Date(`${endsOn}T23:59:59`).getTime();
  const january = new Date(`${endsOn.slice(0, 4)}-01-01T00:00:00`).getTime();
  const start = Math.max(startsOn ? new Date(`${startsOn}T00:00:00`).getTime() : january, january);
  if (end - start < DAY) return 1;
  return Math.min(1, Math.max(0, (today.getTime() - start) / (end - start)));
}

function MembershipTile({ membership }: { membership: MemberAccount['membership'] }) {
  return (
    <Tile
      to="/account/membership"
      title="Membership"
      badge={<Pill tone={membership.tone}>{membership.status_label}</Pill>}
    >
      {membership.activating ? (
        <span className={classes.muted}>Your payment arrived. Confirming it takes a few seconds.</span>
      ) : membership.ends_on ? (
        <>
          <span className={classes.figure}>
            <span className={classes.date}>{formatDate(membership.ends_on)}</span>{' '}
            <span className={classes.muted}>{membership.active ? 'paid until' : 'was paid until'}</span>
          </span>{' '}
          {membership.active ? (
            <span className={classes.bar} aria-hidden>
              <span
                style={{
                  width: `${String(Math.round(passed(membership.starts_on, membership.ends_on) * 100))}%`,
                }}
              />
            </span>
          ) : null}
          <span className={classes.dates}>
            {membership.starts_on ? <span>{`Since ${formatDate(membership.starts_on)}`}</span> : null}{' '}
            {membership.active ? (
              <span>
                {membership.renews_on ? `Renews ${formatDate(membership.renews_on)}` : 'Does not renew'}
              </span>
            ) : null}
          </span>
        </>
      ) : membership.note ? (
        <span className={classes.muted}>{membership.note.text}</span>
      ) : null}
    </Tile>
  );
}

const FORUM_STATES: Record<string, { label: string; tone: Tone }> = {
  active: { label: 'Ready', tone: 'active' },
  needs_avatar: { label: 'Picture needed', tone: 'pending' },
  pending_avatar: { label: 'In review', tone: 'pending' },
  rejected_avatar: { label: 'Picture needed', tone: 'failed' },
  reconnect_waiting: { label: 'Reconnecting', tone: 'pending' },
  payment_processing: { label: 'Waiting', tone: 'pending' },
  inactive_membership: { label: 'Closed', tone: 'neutral' },
  account_disabled: { label: 'Off', tone: 'neutral' },
  disabled: { label: 'Off', tone: 'neutral' },
};

function ForumTile({ forum }: { forum: MemberAccount['forum'] }) {
  const me = useMe().data;
  const state = FORUM_STATES[forum.status];
  const username = forumUsername(forum);
  return (
    <Tile
      to="/account/forum"
      title="Forum and picture"
      badge={state ? <Pill tone={state.tone}>{state.label}</Pill> : null}
    >
      <span className={classes.person}>
        <Avatar src={me?.picture_url} alt="" color="brand" variant="filled" size={52} radius="xl" aria-hidden>
          {me ? initials(me) : null}
        </Avatar>
        <span style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
          {username ? <span className={classes.username}>{username}</span> : null}{' '}
          <span className={classes.muted}>{forum.message}</span>
        </span>
      </span>
    </Tile>
  );
}

function TeamsTile({ teams }: { teams: NonNullable<MemberAccount['teams']> }) {
  return (
    <Tile to="/teams" title={teams.plural} wide>
      {teams.mine.length ? (
        <ul className={classes.teams}>
          {teams.mine.map((team) => (
            <li key={team.slug}>
              <span className={classes.team}>
                <TeamMark name={team.name} logoUrl={team.logo_url} />
                <span>{team.name}</span>
              </span>{' '}
              {team.status_label ? <span className={classes.muted}>{team.status_label}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <span className={classes.muted}>Groups inside the association with their own members.</span>
      )}
      <span className={classes.go}>
        {teams.mine.length ? `All ${teams.plural.toLowerCase()}` : 'See which there are'}
      </span>
    </Tile>
  );
}

function Body({ account }: { account: AccountData }) {
  const member = account.member;
  const teams = member?.teams;
  return (
    <Stack gap="lg">
      <Hero account={account} />
      {/* Without a membership, the address's own card says it and sends the link. */}
      {member ? <ToDo account={account} /> : null}
      {member ? (
        <div className={classes.grid}>
          <MembershipTile membership={member.membership} />
          <ForumTile forum={member.forum} />
          {teams && (teams.mine.length || teams.invite) ? <TeamsTile teams={teams} /> : null}
        </div>
      ) : (
        <div className={classes.grid}>
          <NoMembership />
          <EmailsCard email={account.email} />
        </div>
      )}
    </Stack>
  );
}

export function Account() {
  useDocumentTitle('My Account');
  const account = useAccount();
  return account.isPending ? (
    <LoadingState />
  ) : account.isError ? (
    <ErrorState error={account.error} onRetry={() => void account.refetch()} />
  ) : (
    <Body account={account.data} />
  );
}
