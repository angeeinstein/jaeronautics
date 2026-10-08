/**
 * The account's roles. For whoever manages access, the editor: every role
 * there is (from the permission table, so a new role appears with nothing
 * to change here), what each one lets somebody do, which chosen role already
 * covers another, and why a change would be refused -- said before anybody
 * tries. The rules are the server's (services/access.py).
 */
import { Alert, Checkbox, Group, List, Stack, Text, UnstyledButton } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useState } from 'react';

import { api, call } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { notifyDone, notifyNote } from '../../../lib/notify';
import { type Account, path, useAccountAction } from './shared';
import classes from './Account.module.css';

type Access = NonNullable<Account['access']>;

/** A list kept folded until asked for: a dozen lines above the controls bury the controls. */
function Folded({ label, items }: { label: string; items: string[] }) {
  const [open, { toggle }] = useDisclosure(false);
  return (
    <div>
      <UnstyledButton className={classes.fold} onClick={toggle} aria-expanded={open}>
        {open ? '▾' : '▸'} {label}
      </UnstyledButton>
      {open ? (
        <List size="sm" c="dimmed" mt={4} withPadding>
          {items.map((item) => (
            <List.Item key={item}>{item}</List.Item>
          ))}
        </List>
      ) : null}
    </div>
  );
}

function Editor({ account, access }: { account: Account; access: Access }) {
  const held = access.options.filter((option) => option.held).map((option) => option.slug);
  const [chosen, setChosen] = useState<string[]>(held);
  const unchanged = chosen.length === held.length && chosen.every((slug) => held.includes(slug));
  const save = useAccountAction(
    (roles: string[]) =>
      call(api.PUT('/api/v1/admin/accounts/{user_id}/roles', { ...path(account.id), body: { roles } })),
    {
      done: (answer) => {
        if (answer.changed) notifyDone('Roles saved.');
        else notifyNote('Nothing changed.');
        if (answer.redundant.length)
          notifyNote(
            `${answer.redundant.join(', ')}: covered by another chosen role, so not stored separately.`,
          );
      },
    },
  );

  if (access.roles_locked) {
    return <Alert variant="light">{access.roles_locked}</Alert>;
  }
  return (
    <Stack gap="md">
      {access.removal_warning ? (
        <Alert color="amber" variant="light">
          {access.removal_warning}
        </Alert>
      ) : null}
      <Text size="sm" c="dimmed">
        The account gets the combined permissions of every role chosen here.
      </Text>
      <Checkbox.Group value={chosen} onChange={setChosen} label="Roles">
        <Stack gap="md" mt="xs">
          {access.options.map((option) => (
            <Stack key={option.slug} gap={4}>
              <Checkbox
                value={option.slug}
                disabled={account.erased_at !== null}
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
              <div className={classes.indent}>
                <Folded label="What this role can do" items={option.permissions} />
              </div>
            </Stack>
          ))}
        </Stack>
      </Checkbox.Group>
      <Text size="xs" c="dimmed">
        A role that another chosen role already covers is not stored separately: it would describe the same
        account twice.
      </Text>
      <Group>
        <ConfirmButton
          color="brand"
          confirmLabel="Yes, change the roles"
          disabled={unchanged || account.erased_at !== null}
          loading={save.isPending}
          onConfirm={() => {
            save.mutate(chosen);
          }}
        >
          Save roles
        </ConfirmButton>
      </Group>
    </Stack>
  );
}

export function RolesTab({ account }: { account: Account }) {
  const access = account.access;
  return (
    <Panel title="Roles">
      {access ? (
        <Stack gap="lg">
          {access.effective_permissions.length ? (
            <Folded
              label={`What this account can do now (${String(access.effective_permissions.length)})`}
              items={access.effective_permissions}
            />
          ) : (
            <Text size="sm" c="dimmed">
              No access beyond an ordinary member.
            </Text>
          )}
          {/* Keyed by what is held, so a saved change starts the editor afresh. */}
          <Editor
            key={access.options.map((option) => `${option.slug}:${String(option.held)}`).join(',')}
            account={account}
            access={access}
          />
        </Stack>
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
            Roles are given and taken by whoever manages access.
          </Text>
        </Stack>
      )}
    </Panel>
  );
}
