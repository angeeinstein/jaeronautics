/**
 * The account's access: its roles, and every permission as a row with levels
 * (docs/permissions-plan.md, "The page with levels"). For whoever grants
 * permissions, an editor: ticking a role sets its levels, and each row can
 * then be set higher or lower -- a member given one admin permission, say. A
 * level brings what it needs and goes with it. A super admin holds
 * everything, those added later too: every row at its top, greyed. Who may
 * set what is the server's (services/access.py): only what one holds oneself,
 * for somebody who holds no more.
 */
import { Alert, Checkbox, Group, Stack, Text } from '@mantine/core';
import { useState } from 'react';
import { IconShield } from '@tabler/icons-react';

import { api, call } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Panel } from '../../../components/Panel';
import { PermissionLevels } from '../../../components/PermissionLevels';
import { Pill } from '../../../components/Pill';
import { notifyDone, notifyNote } from '../../../lib/notify';
import {
  type Levels,
  levelsOf,
  permissionsOf,
  pick,
  sameLevels,
  settle,
} from '../../../lib/permissionLevels';
import { type Account, path, useAccountAction } from './shared';

type Access = NonNullable<Account['access']>;
type Role = Access['options'][number];

const SUPERADMIN = 'superadmin';

function sameSet(a: readonly string[], b: readonly string[]) {
  return a.length === b.length && a.every((item) => b.includes(item));
}

/** What the chosen roles give a row: the highest of their levels, or all of it for a super admin. */
function givenBy(roles: readonly Role[], chosen: readonly string[], key: string, top: number): number {
  return Math.max(
    0,
    ...roles
      .filter((role) => chosen.includes(role.slug))
      .map((role) => (role.slug === SUPERADMIN ? top : (role.levels[key] ?? 0))),
  );
}

function Editor({ account, access }: { account: Account; access: Access }) {
  const heldRoles = access.options.filter((option) => option.held).map((option) => option.slug);
  const heldLevels = levelsOf(access.rows);
  const [roles, setRoles] = useState<string[]>(heldRoles);
  const [levels, setLevels] = useState<Levels>(heldLevels);
  const locked = access.roles_locked !== null || account.erased_at !== null;
  const superadmin = roles.includes(SUPERADMIN);
  const unchanged = sameSet(roles, heldRoles) && (superadmin || sameLevels(levels, heldLevels));

  const save = useAccountAction(
    (body: { roles: string[]; permissions: string[] }) =>
      call(api.PUT('/api/v1/admin/accounts/{user_id}/roles', { ...path(account.id), body })),
    {
      done: (answer) => {
        if (answer.changed) notifyDone('Access saved.');
        else notifyNote('Nothing changed.');
        if (answer.redundant.length)
          notifyNote(
            `${answer.redundant.join(', ')}: covered by another chosen role, so not stored separately.`,
          );
      },
    },
  );

  function toggleRole(role: Role, on: boolean) {
    const next = on ? [...roles, role.slug] : roles.filter((slug) => slug !== role.slug);
    const changed: Record<string, number> = { ...levels };
    for (const row of access.rows) {
      const top = row.levels.length;
      const was = givenBy(access.options, roles, row.key, top);
      const now = givenBy(access.options, next, row.key, top);
      const level = changed[row.key] ?? 0;
      // Ticked, a role lifts its rows; unticked, what only it gave goes back -- a row set by
      // hand stays as it was set.
      if (on) changed[row.key] = Math.max(level, now);
      else if (level === was && now < was) changed[row.key] = now;
    }
    setRoles(next);
    setLevels(settle(access.rows, changed));
  }

  return (
    <Stack gap="lg">
      {access.roles_locked ? <Alert variant="light">{access.roles_locked}</Alert> : null}
      {access.removal_warning && !locked ? (
        <Alert color="amber" variant="light">
          {access.removal_warning}
        </Alert>
      ) : null}
      <Stack gap="xs">
        <Text fw={600}>Roles</Text>
        <Text size="sm" c="dimmed">
          Several can be ticked; the account holds them all. A role sets the levels below; each can then be
          changed.
        </Text>
        <Stack gap="sm" mt={4}>
          {access.options.map((option) => (
            <Checkbox
              key={option.slug}
              checked={roles.includes(option.slug)}
              onChange={(event) => {
                toggleRole(option, event.currentTarget.checked);
              }}
              disabled={locked || !option.may_change}
              label={
                <Group gap={6} component="span">
                  {option.label}
                  {option.covered_by ? (
                    <Pill tone="neutral">{`included in ${option.covered_by}`}</Pill>
                  ) : null}
                </Group>
              }
              description={option.description}
            />
          ))}
        </Stack>
      </Stack>

      <Stack gap="xs">
        <Text fw={600}>Permissions</Text>
        <Text size="sm" c="dimmed">
          {superadmin
            ? 'A super admin holds every permission, those added later too, and takes no exceptions.'
            : 'Opening the admin workspace comes with any of these.'}
        </Text>
        <PermissionLevels
          rows={access.rows}
          levels={levels}
          fromRoles={(row) => givenBy(access.options, roles, row.key, row.levels.length)}
          onPick={(key, level) => {
            setLevels(pick(access.rows, levels, key, level));
          }}
          locked={locked}
          everything={superadmin}
        />
      </Stack>

      {locked ? null : (
        <Group>
          <ConfirmButton
            color="brand"
            confirmLabel="Yes, change the access"
            disabled={unchanged}
            loading={save.isPending}
            onConfirm={() => {
              save.mutate({ roles, permissions: superadmin ? [] : permissionsOf(access.rows, levels) });
            }}
          >
            Save access
          </ConfirmButton>
        </Group>
      )}
    </Stack>
  );
}

export function AccessTab({ account }: { account: Account }) {
  const access = account.access;
  return (
    <Panel icon={IconShield} title="Access">
      {access ? (
        // Keyed by what is held, so a saved change starts the editor afresh.
        <Editor
          key={[
            ...access.options.map((option) => `${option.slug}:${String(option.held)}`),
            ...access.rows.map((row) => `${row.key}:${String(row.level)}`),
          ].join(',')}
          account={account}
          access={access}
        />
      ) : (
        <Stack gap="sm">
          {account.roles.length ? (
            <Group gap={4}>
              {account.roles.map((role) => (
                <Pill key={role.slug} tone="neutral">
                  {role.label}
                </Pill>
              ))}
            </Group>
          ) : (
            <Text size="sm">No roles.</Text>
          )}
          <Text size="sm" c="dimmed">
            Access is given and taken by whoever grants permissions.
          </Text>
        </Stack>
      )}
    </Panel>
  );
}
