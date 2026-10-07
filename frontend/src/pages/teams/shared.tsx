/**
 * What the teams pages share: their data, a team's header, and somebody's
 * membership of a team -- its state, what is owed or coming, the buttons --
 * as the server works it out (aeronautics_members/api/teams.py).
 */
import { Alert, Button, Group, Stack, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router';

import { api, call, type Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { ConfirmButton } from '../../components/ConfirmButton';
import { Breadcrumbs, type Crumb, useDocumentTitle } from '../../components/PageHeader';
import { Pill, type Tone } from '../../components/Pill';
import { notifyDone, notifyFailed } from '../../lib/notify';
import classes from './Teams.module.css';

export type Membership = Schemas['TeamMembershipOut'];
export type Team = Schemas['TeamPageOut'];

export const teamsKey = ['teams'] as const;

export function useTeam(slug: string, paid: string | null = null) {
  return useQuery({
    queryKey: [...teamsKey, slug, paid] as const,
    queryFn: () =>
      call(api.GET('/api/v1/teams/{slug}', { params: { path: { slug }, query: paid ? { paid } : {} } })),
  });
}

const TONES: Record<NonNullable<Membership['status']>, Tone> = {
  applied: 'pending',
  invited: 'info',
  approved: 'info',
  active: 'active',
  ended: 'neutral',
  rejected: 'failed',
  withdrawn: 'neutral',
};

export function StatusPill({ membership }: { membership: Membership }) {
  if (!membership.status || !membership.status_label) return null;
  return <Pill tone={TONES[membership.status]}>{membership.status_label}</Pill>;
}

/** A team's logo, large, beside its name at the top of its pages. */
export function TeamLogo({ url }: { url: string | null }) {
  return url ? <img className={classes.logo} src={url} alt="" /> : null;
}

/** Off to Stripe's payment page; it comes back to the overview. */
function usePay(slug: string) {
  return useMutation({
    mutationFn: () => call(api.POST('/api/v1/teams/{slug}/pay', { params: { path: { slug } } })),
    onSuccess: ({ url }) => {
      window.location.assign(url);
    },
    onError: notifyFailed,
  });
}

function useWithdraw(slug: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => call(api.POST('/api/v1/teams/{slug}/withdraw', { params: { path: { slug } } })),
    onSuccess: async ({ message }) => {
      notifyDone(message);
      await client.invalidateQueries({ queryKey: teamsKey });
    },
    onError: notifyFailed,
  });
}

function useStay(slug: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => call(api.POST('/api/v1/teams/{slug}/stay', { params: { path: { slug } } })),
    onSuccess: async ({ message }) => {
      notifyDone(message);
      await client.invalidateQueries({ queryKey: teamsKey });
    },
    onError: notifyFailed,
  });
}

const PAY_LABELS = {
  pay_join: 'Pay and join',
  pay_next: 'Pay for next period',
  pay_stay: 'Pay to stay',
} as const;

/**
 * The state's notes and its buttons. On a team's own pages (``onTeamPage``)
 * there is no *Open*, and joining is the form on the page, not a button.
 */
export function MembershipBlock({
  slug,
  membership,
  onTeamPage = false,
}: {
  slug: string;
  membership: Membership;
  onTeamPage?: boolean;
}) {
  const navigate = useNavigate();
  const pay = usePay(slug);
  const withdraw = useWithdraw(slug);
  const stay = useStay(slug);
  const actions = membership.actions.filter(
    (action) => !(onTeamPage && (action === 'open' || action === 'join')),
  );
  const payAction = actions.find((action) => action in PAY_LABELS) as keyof typeof PAY_LABELS | undefined;

  return (
    <Stack gap="sm">
      {membership.fee ? <Text size="sm" c="dimmed">{`Fee: ${membership.fee}.`}</Text> : null}
      {membership.notes.map((note) =>
        note.tone === 'plain' ? (
          <Text key={note.text} size="sm">
            {note.text}
          </Text>
        ) : (
          <Alert key={note.text} color={note.tone === 'warning' ? 'amber' : 'brand'} variant="light">
            {note.text}
          </Alert>
        ),
      )}
      {membership.meeting ? (
        <div>
          <Text size="sm" c="dimmed">
            The leads would like to meet you:
          </Text>
          <Text size="sm" className={classes.preLine}>
            {membership.meeting}
          </Text>
        </div>
      ) : null}
      {!onTeamPage && membership.why_not ? (
        <Text size="sm" c="dimmed">
          {membership.why_not}
        </Text>
      ) : null}
      {actions.length ? (
        <Group gap="xs">
          {actions.map((action) => {
            switch (action) {
              case 'open':
                return (
                  <Button
                    key={action}
                    component={AppLink}
                    to={`/teams/${slug}`}
                    variant={payAction ? 'default' : 'filled'}
                  >
                    Open
                  </Button>
                );
              case 'pay_join':
              case 'pay_next':
              case 'pay_stay':
                return (
                  <Button
                    key={action}
                    loading={pay.isPending}
                    onClick={() => {
                      pay.mutate();
                    }}
                  >
                    {PAY_LABELS[action]}
                  </Button>
                );
              case 'join':
                return (
                  <Button key={action} component={AppLink} to={`/teams/${slug}/about#join`}>
                    {membership.join_label ?? 'Join'}
                  </Button>
                );
              case 'withdraw':
                return (
                  <ConfirmButton
                    key={action}
                    confirmLabel="Yes, withdraw"
                    loading={withdraw.isPending}
                    onConfirm={() => {
                      withdraw.mutate();
                    }}
                  >
                    Withdraw application
                  </ConfirmButton>
                );
              case 'manage':
                return (
                  <Button key={action} component={AppLink} to={`/teams/${slug}/manage`} variant="default">
                    Manage
                  </Button>
                );
              case 'money':
                return (
                  <Button key={action} component={AppLink} to={`/teams/${slug}/money`} variant="default">
                    Money
                  </Button>
                );
              case 'stay':
                return (
                  <Button
                    key={action}
                    variant="default"
                    loading={stay.isPending}
                    onClick={() => {
                      stay.mutate();
                    }}
                  >
                    Stay after all
                  </Button>
                );
              case 'leave':
                // Set apart: leaving is never the obvious next step.
                return (
                  <Button
                    key={action}
                    variant="subtle"
                    color="gray"
                    ml="auto"
                    onClick={() => void navigate(`/teams/${slug}/leave`)}
                  >
                    Leave
                  </Button>
                );
            }
          })}
        </Group>
      ) : null}
    </Stack>
  );
}

/** The top of a team's own pages: the way back, its logo and name, its state, and the way elsewhere. */
export function TeamTitle({
  team,
  page,
  children,
}: {
  team: Team;
  /** The page below the team, for the breadcrumb and the tab: "About", "Leave". */
  page?: string;
  children?: ReactNode;
}) {
  useDocumentTitle(page ? `${team.name}: ${page}` : team.name);
  const crumbs: Crumb[] = [{ label: team.labels.plural, to: '/teams' }];
  crumbs.push(page ? { label: team.name, to: `/teams/${team.slug}` } : { label: team.name });
  if (page) crumbs.push({ label: page });
  return (
    <Stack gap="xs" mb="lg">
      <Breadcrumbs crumbs={crumbs} />
      <Group justify="space-between" align="flex-start" gap="md">
        <Group gap="md" wrap="nowrap">
          <TeamLogo url={team.logo_url} />
          <Stack gap={4}>
            <Title order={1}>{team.name}</Title>
            {team.description ? <Text c="dimmed">{team.description}</Text> : null}
          </Stack>
        </Group>
        <Group gap="xs">
          <StatusPill membership={team.membership} />
          {children}
        </Group>
      </Group>
    </Stack>
  );
}
