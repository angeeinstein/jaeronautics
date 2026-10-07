/**
 * A team's people, for its leads: who applies (also the management's first
 * page), who is in the team, and who was -- each a way to the person's page.
 * Data: GET /api/v1/teams/<slug>/manage/applications, /members, /former.
 */
import { Anchor, Avatar, Button, Group, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Navigate, useLocation } from 'react-router';

import { api, call } from '../../../api/client';
import { AppLink } from '../../../app/AppLink';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { formatDate, formatDayOf } from '../../../lib/format';
import { ManageHeader, useSlug } from './shared';

/** Old links to a tab of the management page (#manage-applying) open that section's page. */
const MOVED: Record<string, string> = {
  '#manage-members': 'members',
  '#manage-former': 'former',
  '#manage-page': 'page',
  '#manage-applying': 'applying',
  '#manage-access-list': 'access-list',
  '#manage-roles': 'roles',
};

function personLink(slug: string, userId: number) {
  return `/teams/${slug}/manage/people/${String(userId)}`;
}

const TONES = { applied: 'pending', invited: 'info', approved: 'info' } as const;

export function Applications() {
  const slug = useSlug();
  const { hash } = useLocation();
  const moved = MOVED[hash];
  const applications = useQuery({
    queryKey: ['teams', slug, 'manage', 'applications'] as const,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/applications', { params: { path: { slug } } })),
    enabled: !moved,
  });
  if (moved) return <Navigate to={`/teams/${slug}/manage/${moved}`} replace />;
  return (
    <>
      <ManageHeader title="Applications" description="Who has applied, been invited, or is paying to join." />
      <Panel title="Waiting" flush>
        {applications.isPending ? (
          <LoadingState />
        ) : applications.isError ? (
          <ErrorState error={applications.error} onRetry={() => void applications.refetch()} />
        ) : applications.data.applications.length ? (
          <Table.ScrollContainer minWidth={520} type="native">
            <Table verticalSpacing="sm" aria-label="Applications">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Name</Table.Th>
                  <Table.Th scope="col">State</Table.Th>
                  <Table.Th scope="col">Applied</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {applications.data.applications.map((row) => (
                  <Table.Tr key={row.user_id}>
                    <Table.Td>
                      <Anchor component={AppLink} to={personLink(slug, row.user_id)}>
                        {row.name}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>
                      <Stack gap={2}>
                        <div>
                          <Pill tone={TONES[row.status]}>{row.status_label}</Pill>
                        </div>
                        {row.payment ? (
                          <Text size="xs" c="dimmed">
                            {row.payment}
                          </Text>
                        ) : null}
                      </Stack>
                    </Table.Td>
                    <Table.Td>{row.applied_on ? formatDate(row.applied_on) : '–'}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ) : (
          <Stack px="md">
            <EmptyState>None waiting.</EmptyState>
          </Stack>
        )}
      </Panel>
    </>
  );
}

export function Members() {
  const slug = useSlug();
  const members = useQuery({
    queryKey: ['teams', slug, 'manage', 'members'] as const,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/members', { params: { path: { slug } } })),
  });
  const data = members.data;
  return (
    <>
      <ManageHeader
        title="Members"
        description="Who is in the team, by surname."
        actions={
          data?.may_export ? (
            // A download from Flask, which logs it.
            <Button component="a" href={`/teams/${slug}/manage/export.csv`} variant="default">
              Export members
            </Button>
          ) : null
        }
      />
      <Panel
        title="Members"
        flush
        actions={
          data ? (
            <Text size="sm" c="dimmed">
              {data.max_members
                ? `${String(data.members.length)} / ${String(data.max_members)}`
                : String(data.members.length)}
            </Text>
          ) : null
        }
      >
        {members.isPending ? (
          <LoadingState />
        ) : members.isError ? (
          <ErrorState error={members.error} onRetry={() => void members.refetch()} />
        ) : members.data.members.length ? (
          <Table.ScrollContainer minWidth={760} type="native">
            <Table verticalSpacing="sm" aria-label="Members">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Name</Table.Th>
                  <Table.Th scope="col">University email</Table.Th>
                  <Table.Th scope="col">Cohort</Table.Th>
                  <Table.Th scope="col">Since</Table.Th>
                  {members.data.charges ? <Table.Th scope="col">Paid until</Table.Th> : null}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {members.data.members.map((row) => (
                  <Table.Tr key={row.user_id}>
                    <Table.Td>
                      <Group gap="sm" wrap="nowrap">
                        <Avatar src={row.picture_url} alt="" radius={0} size={32} aria-hidden>
                          {row.name.charAt(0)}
                        </Avatar>
                        <Anchor component={AppLink} to={personLink(slug, row.user_id)}>
                          {row.name}
                        </Anchor>
                        {row.is_lead ? <Pill tone="info">Lead</Pill> : null}
                      </Group>
                    </Table.Td>
                    <Table.Td>{row.university_email ?? '–'}</Table.Td>
                    <Table.Td>{row.cohort ?? '–'}</Table.Td>
                    <Table.Td>{row.since ? formatDate(row.since) : '–'}</Table.Td>
                    {members.data.charges ? (
                      <Table.Td>
                        {row.paid_until ? formatDate(row.paid_until) : '–'}
                        {row.notes.map((note) => (
                          <Text key={note} size="xs" c="var(--ja-warning)">
                            {note}
                          </Text>
                        ))}
                      </Table.Td>
                    ) : null}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ) : (
          <Stack px="md">
            <EmptyState>Nobody yet.</EmptyState>
          </Stack>
        )}
      </Panel>
    </>
  );
}

export function FormerMembers() {
  const slug = useSlug();
  const former = useQuery({
    queryKey: ['teams', slug, 'manage', 'former'] as const,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/former', { params: { path: { slug } } })),
  });
  return (
    <>
      <ManageHeader
        title="Former members"
        description="Everybody who was in the team and is not now, kept for good."
      />
      <Panel title="Former members" flush>
        {former.isPending ? (
          <LoadingState />
        ) : former.isError ? (
          <ErrorState error={former.error} onRetry={() => void former.refetch()} />
        ) : former.data.former.length ? (
          <Table.ScrollContainer minWidth={620} type="native">
            <Table verticalSpacing="sm" aria-label="Former members">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Name</Table.Th>
                  <Table.Th scope="col">In the team</Table.Th>
                  <Table.Th scope="col">Last active</Table.Th>
                  <Table.Th scope="col">Why it ended</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {former.data.former.map((row) => (
                  <Table.Tr key={row.user_id}>
                    <Table.Td>
                      <Anchor component={AppLink} to={personLink(slug, row.user_id)}>
                        {row.name}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>{row.periods}</Table.Td>
                    <Table.Td>{row.last_active ? formatDayOf(row.last_active) : '–'}</Table.Td>
                    <Table.Td>{row.why ?? '–'}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ) : (
          <Stack px="md">
            <EmptyState>None.</EmptyState>
          </Stack>
        )}
      </Panel>
    </>
  );
}
