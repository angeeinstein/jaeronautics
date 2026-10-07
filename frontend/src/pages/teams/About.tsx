/**
 * What a team is about, for everybody signed in: its longer text and
 * picture, then joining -- or applying, with the leads' question -- with its
 * rules to accept, read in a dialog without leaving the form. For a member,
 * their membership instead. Data: GET /api/v1/teams/<slug>,
 * POST .../join.
 */
import { Alert, Anchor, Button, Checkbox, Group, Stack, Text, Textarea } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { type SyntheticEvent, useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';

import { api, ApiError, call } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { Panel } from '../../components/Panel';
import { PdfLink } from '../../components/PdfLink';
import { ErrorState, LoadingState } from '../../components/States';
import { formatDate } from '../../lib/format';
import { notifyDone } from '../../lib/notify';
import { RulesDialog, RulesLine } from './Rules';
import { MembershipBlock, type Team, TeamTitle, teamsKey, useTeam } from './shared';
import classes from './Teams.module.css';

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

export function About() {
  const { slug = '' } = useParams();
  const { hash } = useLocation();
  const team = useTeam(slug);
  // A link to #join (Join, Apply on the overview) lands on the form once it is there.
  useEffect(() => {
    if (hash === '#join' && team.data) document.getElementById('join')?.scrollIntoView();
  }, [hash, team.data]);
  if (team.isPending) return <LoadingState />;
  if (team.isError) return <ErrorState error={team.error} onRetry={() => void team.refetch()} />;
  const data = team.data;
  const active = data.membership.status === 'active' && data.membership.ongoing;
  const joining = data.joining;
  return (
    <>
      <TeamTitle team={data} page="About">
        {data.sees_team_page ? (
          <Button component={AppLink} to={`/teams/${slug}`}>
            Team page
          </Button>
        ) : null}
        <Button component={AppLink} to="/teams" variant="default">
          {`All ${data.labels.plural}`}
        </Button>
      </TeamTitle>
      <Stack gap="lg">
        {data.about || data.picture_url ? (
          <Panel title="About the team">
            {data.picture_url ? (
              <img className={classes.picture} src={data.picture_url} alt={`Picture of ${data.name}`} />
            ) : null}
            {data.about ? <Text className={classes.preLine}>{data.about}</Text> : null}
          </Panel>
        ) : null}
        <div id="join">
          <Panel
            title={
              active
                ? 'Membership'
                : data.membership.ongoing
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
              <MembershipBlock slug={slug} membership={data.membership} onTeamPage />
              {joining ? (
                <JoinForm team={data} />
              ) : data.rules ? (
                <RulesLine slug={slug} name={data.name} rules={data.rules} />
              ) : null}
            </Stack>
          </Panel>
        </div>
      </Stack>
    </>
  );
}
