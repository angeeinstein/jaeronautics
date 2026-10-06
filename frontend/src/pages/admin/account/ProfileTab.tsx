/**
 * Who the account is: the address it signs in with, its forum name, its state
 * and roles. And, for somebody locked out by a wrong private address, the
 * correction (services/account_admin.py, correct_private_email).
 */
import { Button, Group, Stack, Text, TextInput } from '@mantine/core';
import { useState } from 'react';

import { api, ApiError, call } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Details } from '../../../components/Details';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { formatDateTime } from '../../../lib/format';
import { notifyDone } from '../../../lib/notify';
import { ACCOUNT_STATE_PILL } from '../accountLabels';
import { type Account, path, useAccountAction } from './shared';

function AccountState({ account }: { account: Account }) {
  const pill = ACCOUNT_STATE_PILL[account.account_state];
  return pill ? <Pill tone={pill.tone}>{pill.label}</Pill> : <Pill tone="active">Active</Pill>;
}

function CorrectEmail({ account }: { account: Account }) {
  const [email, setEmail] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const change = useAccountAction(
    (address: string) =>
      call(
        api.PUT('/api/v1/admin/accounts/{user_id}/email', { ...path(account.id), body: { email: address } }),
      ),
    {
      done: (answer) => {
        setEmail('');
        notifyDone(`The address is now ${answer.email}. A confirmation link went there.`);
      },
      onError: (error) => {
        const message = error instanceof ApiError ? error.fields.email : undefined;
        if (message) setProblem(message);
        return Boolean(message);
      },
    },
  );

  return (
    <Panel title="Correct the private address">
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          For a member who cannot get in because the address is wrong and the password forgotten. Whoever
          reads the new address can take over the account, so change it only once you know who is asking. A
          confirmation link goes to the new address, and the old one is told if it was ever confirmed.
        </Text>
        <Group align="flex-start" gap="sm">
          <TextInput
            type="email"
            label="New private address"
            value={email}
            error={problem}
            onChange={(event) => {
              setEmail(event.currentTarget.value);
              setProblem(null);
            }}
            flex="1 1 18rem"
          />
          <ConfirmButton
            mt={25}
            color="brand"
            confirmLabel="Yes, change it"
            disabled={!email.trim()}
            loading={change.isPending}
            onConfirm={() => {
              change.mutate(email.trim());
            }}
          >
            Change address
          </ConfirmButton>
        </Group>
      </Stack>
    </Panel>
  );
}

export function ProfileTab({ account }: { account: Account }) {
  return (
    <Stack gap="lg">
      <Panel title="Account">
        <Details
          items={[
            ['Name', account.name],
            [
              'Signs in as',
              <>
                {account.email}{' '}
                {account.email_verified ? null : (
                  <Text span size="sm" c="var(--ja-warning)">
                    (never confirmed)
                  </Text>
                )}
              </>,
            ],
            ['Forum username', account.forum_username],
            ['Account', <AccountState key="state" account={account} />],
            ...(account.disabled_at
              ? ([
                  ['Deactivated', formatDateTime(account.disabled_at)],
                  ['Reason', account.disabled_reason ?? 'None recorded'],
                ] as [string, string][])
              : []),
            ...(account.erased_at
              ? ([['Erased', formatDateTime(account.erased_at)]] as [string, string][])
              : []),
            [
              'Roles',
              account.roles.length ? (
                <Group gap={4} key="roles">
                  {account.roles.map((role) => (
                    <Pill key={role.slug} tone="neutral">
                      {role.label}
                    </Pill>
                  ))}
                </Group>
              ) : (
                'None'
              ),
            ],
          ]}
        />
        {account.actions.export_data ? (
          <Button
            component="a"
            href={`/admin/accounts/${String(account.id)}/data-export`}
            variant="default"
            size="xs"
            mt="md"
          >
            Download everything stored (JSON)
          </Button>
        ) : null}
      </Panel>
      {account.actions.correct_email ? <CorrectEmail account={account} /> : null}
    </Stack>
  );
}
