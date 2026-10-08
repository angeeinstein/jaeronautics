/**
 * A team presenting itself to everybody signed in: a cover with its name, what
 * it is in a line and a few facts (how many are in it, how to get in, what it
 * costs); then its story, formatted, and its photos, with joining -- or
 * applying, with the leads' question and the rules to accept, read in a dialog
 * without leaving the form -- beside them. A member reads it in the team's
 * frame, without the form: their membership is on the team's overview.
 * Data: GET /api/v1/teams/<slug>, POST .../join.
 */
import { Alert, Anchor, Button, Checkbox, Group, Stack, Text, Textarea } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { type SyntheticEvent, useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';

import { api, ApiError, call } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { Breadcrumbs, useDocumentTitle } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { PdfLink } from '../../components/PdfLink';
import { ErrorState, LoadingState } from '../../components/States';
import { formatDate } from '../../lib/format';
import { notifyDone } from '../../lib/notify';
import classes from './About.module.css';
import { Gallery } from './Gallery';
import { RulesDialog, RulesLine } from './Rules';
import { MembershipBlock, type Team, teamsKey, useTeam } from './shared';
import { canJoin, TeamHero } from './TeamHero';

function JoinForm({ team }: { team: Team }) {
  const joining = team.joining;
  const client = useQueryClient();
  const navigate = useNavigate();
  const [text, setText] = useState('');
  const [accepted, setAccepted] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [rulesOpen, rulesDialog] = useDisclosure(false);
  const join = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/teams/{slug}/join', {
          params: { path: { slug: team.slug } },
          body: { application_text: text || null, accept_rules: accepted },
        }),
      ),
    onSuccess: async ({ message, team: fresh }) => {
      notifyDone(message);
      await client.invalidateQueries({ queryKey: teamsKey });
      if (fresh.sees_team_page) void navigate(`/teams/${team.slug}`);
    },
    onError: (error) => {
      setProblem(error instanceof ApiError ? error.message : 'That did not work. Please try again.');
    },
  });
  if (!joining) return null;
  if (!team.is_member) return <Text size="sm">Teams are for members of the association.</Text>;
  if (joining.why_not)
    return (
      <Text size="sm" c="dimmed">
        {joining.why_not}
      </Text>
    );

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    setProblem(null);
    join.mutate();
  };
  return (
    <form onSubmit={submit}>
      <Stack gap="md">
        {joining.prompt ? (
          <Textarea
            label={joining.prompt}
            autosize
            minRows={4}
            maxLength={5000}
            value={text}
            onChange={(event) => {
              setText(event.currentTarget.value);
            }}
          />
        ) : null}
        {team.rules ? (
          <Stack gap={4}>
            <Checkbox
              checked={accepted}
              onChange={(event) => {
                setAccepted(event.currentTarget.checked);
              }}
              label={
                <>
                  I accept the{' '}
                  <Anchor
                    component="button"
                    type="button"
                    size="sm"
                    td="underline"
                    onClick={rulesDialog.open}
                  >{`rules of ${team.name}`}</Anchor>{' '}
                  {`(version of ${formatDate(team.rules.version)}).`}
                </>
              }
            />
            <Text size="xs" pl={32}>
              <PdfLink href={`/teams/${team.slug}/rules/pdf`}>Rules as PDF</PdfLink>
            </Text>
            <RulesDialog slug={team.slug} opened={rulesOpen} onClose={rulesDialog.close} />
          </Stack>
        ) : null}
        {problem ? (
          <Alert color="red" variant="light" role="alert">
            {problem}
          </Alert>
        ) : null}
        <Group>
          <Button type="submit" loading={join.isPending} disabled={Boolean(team.rules) && !accepted}>
            {joining.submit_label}
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

/** On its cover: applying, for whoever could. Everything else is in the team's menu. */
function Hero({ team }: { team: Team }) {
  const joining = team.joining;
  return (
    <TeamHero team={team}>
      {joining && canJoin(team) ? (
        <Group gap="xs">
          <Button component="a" href="#join">
            {joining.submit_label}
          </Button>
        </Group>
      ) : null}
    </TeamHero>
  );
}

function Joining({ team }: { team: Team }) {
  const active = team.membership.status === 'active' && team.membership.ongoing;
  const joining = team.joining;
  return (
    <Panel
      title={
        active
          ? 'Membership'
          : team.membership.ongoing
            ? 'Your application'
            : joining?.mode === 'approval'
              ? 'Applying'
              : 'Joining'
      }
    >
      <Stack gap="md">
        {joining?.rejoin_until ? (
          <Text size="sm">
            {`Your membership ended because it was not paid. Until ${formatDate(joining.rejoin_until)} you can come back by paying again — no new application needed.`}
          </Text>
        ) : joining?.mode === 'approval' ? (
          <Text size="sm" c="dimmed">
            You apply here; the leads get in touch, may invite you to meet, and decide.
          </Text>
        ) : null}
        <MembershipBlock slug={team.slug} membership={team.membership} onTeamPage />
        {joining ? (
          <JoinForm team={team} />
        ) : team.rules ? (
          <RulesLine slug={team.slug} name={team.name} rules={team.rules} />
        ) : null}
      </Stack>
    </Panel>
  );
}

export function About() {
  const { slug = '' } = useParams();
  const { hash } = useLocation();
  const team = useTeam(slug);
  useDocumentTitle(team.data ? `${team.data.name}: About` : 'About');
  // A link to #join (Join, Apply on the overview) lands on the form once it is there.
  useEffect(() => {
    if (hash === '#join' && team.data) document.getElementById('join')?.scrollIntoView();
  }, [hash, team.data]);
  if (team.isPending) return <LoadingState />;
  if (team.isError) return <ErrorState error={team.error} onRetry={() => void team.refetch()} />;
  const data = team.data;
  const told = Boolean(data.about_html) || data.photos.length > 0;
  // Joining, or one's membership, beside the story -- for whoever is not in the
  // team: a member has their membership on the team's overview.
  const aside = !data.sees_team_page;
  return (
    <>
      <Stack gap="xs" mb="md">
        <Breadcrumbs
          crumbs={
            data.sees_team_page
              ? [
                  { label: data.labels.plural, to: '/teams' },
                  { label: data.name, to: `/teams/${slug}` },
                  { label: 'About' },
                ]
              : [{ label: data.labels.plural, to: '/teams' }, { label: data.name }]
          }
        />
      </Stack>
      <Hero team={data} />
      <div className={classes.layout} data-aside={(told && aside) || undefined}>
        {told ? (
          <Stack gap="xl">
            {data.about_html ? (
              // Formatted on the server from Markdown without HTML (services/teams.py, render_about).
              <div className={classes.story} dangerouslySetInnerHTML={{ __html: data.about_html }} />
            ) : null}
            <Gallery photos={data.photos} teamName={data.name} />
          </Stack>
        ) : aside ? null : (
          <Text c="dimmed">
            {'Nothing has been written about the team yet. '}
            {data.can_edit_page ? (
              <Anchor component={AppLink} to={`/teams/${slug}/manage/page`}>
                Write it
              </Anchor>
            ) : null}
          </Text>
        )}
        {aside ? (
          <div id="join" className={classes.aside}>
            <Joining team={data} />
          </div>
        ) : null}
      </div>
    </>
  );
}
