/**
 * The teams this person is in, has applied to or has left, and their roles
 * in them. Each still-running team links to the person's page in its
 * management.
 */
import { Group, Stack, Table } from '@mantine/core';

import { AppLink } from '../../../app/AppLink';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { EmptyState } from '../../../components/States';
import { formatDayOf } from '../../../lib/format';
import type { Account } from './shared';

export function TeamsTab({ teams, title }: { teams: NonNullable<Account['teams']>; title: string }) {
  if (!teams.roles.length && !teams.memberships.length) {
    return (
      <Panel title={title}>
        <EmptyState>Not in any team, and never applied to one.</EmptyState>
      </Panel>
    );
  }
  return (
    <Stack gap="lg">
      {teams.roles.length ? (
        <Panel title="Roles in teams">
          <Group gap={6}>
            {teams.roles.map((role) => (
              <Pill key={`${role.team}-${role.role_label}`} tone="info">
                {`${role.role_label} · ${role.team}`}
              </Pill>
            ))}
          </Group>
        </Panel>
      ) : null}
      {teams.memberships.length ? (
        <Panel title={title} flush>
          <Table.ScrollContainer minWidth={560} type="native">
            <Table verticalSpacing="sm" aria-label={title}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Team</Table.Th>
                  <Table.Th scope="col">Status</Table.Th>
                  <Table.Th scope="col">Since</Table.Th>
                  <Table.Th scope="col">Until</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {teams.memberships.map((membership, index) => (
                  <Table.Tr key={`${membership.team}-${String(index)}`}>
                    <Table.Td>
                      {membership.link_url ? (
                        <AppLink to={membership.link_url}>{membership.team}</AppLink>
                      ) : (
                        membership.team
                      )}
                    </Table.Td>
                    <Table.Td>
                      {membership.status_label}
                      {membership.end_reason_label ? ` · ${membership.end_reason_label}` : ''}
                    </Table.Td>
                    <Table.Td className="ja-figures">
                      {membership.since ? formatDayOf(membership.since) : '–'}
                    </Table.Td>
                    <Table.Td className="ja-figures">
                      {membership.until ? formatDayOf(membership.until) : '–'}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Panel>
      ) : null}
    </Stack>
  );
}
