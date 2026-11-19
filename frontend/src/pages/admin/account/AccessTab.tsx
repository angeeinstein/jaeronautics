/**
 * The account's access: its roles, and every permission grouped by area
 * (docs/permissions-plan.md). For whoever grants permissions, an editor:
 * ticking a role ticks what it gives, and each permission can then be
 * unticked, or ticked without a role -- a member given one admin permission,
 * say. A permission brings what it needs and goes with it. A super admin
 * holds everything, those added later too: all ticked and greyed. Who may
 * tick what is the server's (services/access.py): only what one holds
 * oneself, for somebody who holds no more.
 */
import { Alert, Checkbox, Group, SimpleGrid, Stack, Text } from '@mantine/core';
import { useState } from 'react';
import { IconShield } from '@tabler/icons-react';

import { api, call } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { notifyDone, notifyNote } from '../../../lib/notify';
import { type Account, path, useAccountAction } from './shared';
import { groupByArea, untick, withNeeds, withoutUnmet } from '../../../lib/permissionSets';

type Access = NonNullable<Account['access']>;
type Role = Access['options'][number];

const SUPERADMIN = 'superadmin';

function sameSet(a: Iterable<string>, b: Iterable<string>) {
  const left = new Set(a);
  const right = new Set(b);
  return left.size === right.size && [...left].every((item) => right.has(item));
}

function givenBy(roles: Role[], chosen: string[]) {
  return new Set(roles.filter((role) => chosen.includes(role.slug)).flatMap((role) => role.permission_names));
}

function Editor({ account, access }: { account: Account; access: Access }) {
  const needs = new Map(access.permissions.map((option) => [option.name, option.needs]));
  const heldRoles = access.options.filter((option) => option.held).map((option) => option.slug);
  const heldPermissions = access.permissions.filter((option) => option.held).map((option) => option.name);
  const [roles, setRoles] = useState<string[]>(heldRoles);
  const [permissions, setPermissions] = useState<Set<string>>(new Set(heldPermissions));
  const locked = access.roles_locked !== null || account.erased_at !== null;
  const superadmin = roles.includes(SUPERADMIN);
  const fromRoles = givenBy(access.options, roles);
  const unchanged = sameSet(roles, heldRoles) && (superadmin || sameSet(permissions, heldPermissions));

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
    setRoles(next);
    if (on) {
      setPermissions(withNeeds([...permissions, ...role.permission_names], needs));
    } else {
      // What only this role gave goes; what another chosen role gives, or was added, stays.
      const still = givenBy(access.options, next);
      const gone = role.permission_names.filter((name) => !still.has(name));
      setPermissions(
        withoutUnmet(
          [...permissions].filter((name) => !gone.includes(name)),
          needs,
        ),
      );
    }
  }

  function togglePermission(name: string, on: boolean) {
    setPermissions(on ? withNeeds([...permissions, name], needs) : untick(permissions, name, needs));
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
          Several can be ticked; the account holds them all. A role ticks its permissions below; each can then
          be unticked, or another added.
        </Text>
        <Stack gap="md" mt={4}>
          {access.options.map((option) => (
            <Stack key={option.slug} gap={4}>
              <Checkbox
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
            </Stack>
          ))}
        </Stack>
      </Stack>

      <Stack gap="md">
        <Text fw={600}>Permissions</Text>
        {superadmin ? (
          <Text size="sm" c="dimmed">
            A super admin holds every permission, those added later too, and takes no exceptions.
          </Text>
        ) : null}
        {groupByArea(access.permissions).map(([area, options]) => (
          <Stack key={area} gap={6}>
            <Group gap={8}>
              <Text size="sm" fw={600}>
                {area}
              </Text>
              <Text size="xs" c="dimmed">
                {superadmin
                  ? `${String(options.length)} of ${String(options.length)}`
                  : `${String(options.filter((option) => permissions.has(option.name)).length)} of ${String(options.length)}`}
              </Text>
            </Group>
            <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md" verticalSpacing={6}>
              {options.map((option) => {
                const ticked = superadmin || permissions.has(option.name);
                const byRole = fromRoles.has(option.name);
                return (
                  <Checkbox
                    key={option.name}
                    checked={ticked}
                    onChange={(event) => {
                      togglePermission(option.name, event.currentTarget.checked);
                    }}
                    disabled={locked || superadmin || !option.may_change}
                    label={
                      <Group gap={6} component="span">
                        {option.label}
                        {!superadmin && ticked && !byRole ? <Pill tone="info">added</Pill> : null}
                        {!superadmin && !ticked && byRole ? <Pill tone="pending">taken away</Pill> : null}
                      </Group>
                    }
                  />
                );
              })}
            </SimpleGrid>
          </Stack>
        ))}
      </Stack>

      {locked ? null : (
        <Group>
          <ConfirmButton
            color="brand"
            confirmLabel="Yes, change the access"
            disabled={unchanged}
            loading={save.isPending}
            onConfirm={() => {
              save.mutate({ roles, permissions: [...permissions].sort() });
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
            ...access.permissions.map((option) => `${option.name}:${String(option.held)}`),
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
