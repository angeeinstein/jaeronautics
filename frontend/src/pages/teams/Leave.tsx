/**
 * Leaving a team is a page of its own: what it means for this person, then a
 * deliberate yes. Data: GET|POST /api/v1/teams/<slug>/leave.
 */
import { Alert, Button, Checkbox, Group, List, Stack, Textarea } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type SyntheticEvent, useState } from 'react';
import { useNavigate, useParams } from 'react-router';

import { api, ApiError, call } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { ErrorState, LoadingState } from '../../components/States';
import { formatDate } from '../../lib/format';
import { notifyDone } from '../../lib/notify';
import { teamsKey } from './shared';

export function Leave() {
  const { slug = '' } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [message, setMessage] = useState('');
  const [sure, setSure] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const leaving = useQuery({
    queryKey: [...teamsKey, slug, 'leave'] as const,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/leave', { params: { path: { slug } } })),
  });
  const leave = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/teams/{slug}/leave', {
          params: { path: { slug } },
          body: { message: message || null, confirm: true },
        }),
      ),
    onSuccess: async ({ message: done }) => {
      notifyDone(done);
      await client.invalidateQueries({ queryKey: teamsKey });
      void navigate('/teams');
    },
    onError: (error) => {
      setProblem(error instanceof ApiError ? error.message : 'That did not work. Please try again.');
    },
  });

  const name = leaving.data?.team.name ?? '';
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    leave.mutate();
  };
  return (
    <>
      <PageHeader
        title={name ? `Leave ${name}` : 'Leave'}
        description="Please read what leaving means before you decide."
        crumbs={[
          { label: leaving.data?.labels.plural ?? 'Teams', to: '/teams' },
          ...(name ? [{ label: name, to: `/teams/${slug}` }] : []),
          { label: 'Leave' },
        ]}
      />
      {leaving.isPending ? (
        <LoadingState />
      ) : leaving.isError ? (
        <ErrorState error={leaving.error} />
      ) : (
        <Panel title="What leaving means">
          <Stack gap="md" maw={640}>
            <List size="sm" spacing="xs">
              {leaving.data.stays_until ? (
                <>
                  <List.Item>
                    {`You stay in the team until ${formatDate(leaving.data.stays_until)}, the end of what you have paid for.${leaving.data.subscription ? ' Your subscription stops then and nothing more is charged.' : ''} There is no refund.`}
                  </List.Item>
                  <List.Item>Until then you can take it back on the Teams page.</List.Item>
                </>
              ) : (
                <List.Item>You leave at once.</List.Item>
              )}
              <List.Item>
                {leaving.data.access_list
                  ? 'You are taken off the team page, its forum group and the list for access to its rooms.'
                  : 'You are taken off the team page and its forum group.'}
              </List.Item>
              {leaving.data.is_lead ? (
                <List.Item>Your role as a lead ends with your membership.</List.Item>
              ) : null}
              <List.Item>To come back later, you apply again and the leads decide.</List.Item>
            </List>
            <form onSubmit={submit}>
              <Stack gap="md">
                <Textarea
                  label="A message to the leads (optional)"
                  autosize
                  minRows={3}
                  maxLength={1000}
                  value={message}
                  onChange={(event) => {
                    setMessage(event.currentTarget.value);
                  }}
                />
                <Checkbox
                  label={`Yes, I want to leave ${name}.`}
                  checked={sure}
                  onChange={(event) => {
                    setSure(event.currentTarget.checked);
                  }}
                />
                {problem ? (
                  <Alert color="red" variant="light" role="alert">
                    {problem}
                  </Alert>
                ) : null}
                <Group>
                  <Button
                    type="submit"
                    color="red"
                    variant="outline"
                    disabled={!sure}
                    loading={leave.isPending}
                  >
                    Leave the team
                  </Button>
                  <Button component={AppLink} to="/teams" variant="default">
                    Cancel
                  </Button>
                </Group>
              </Stack>
            </form>
          </Stack>
        </Panel>
      )}
    </>
  );
}
