/**
 * Settings -> Mail accounts: the accounts the portal sends email from --
 * added, changed, tested, removed -- and importing or exporting them as a
 * JSON file. Their passwords never reach the browser, except in an export,
 * which asks for the current password first. Data: /api/v1/admin/settings/mail
 * (aeronautics_members/api/admin_mail.py).
 */
import {
  Button,
  Checkbox,
  FileInput,
  Group,
  NumberInput,
  PasswordInput,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  VisuallyHidden,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, ApiError, call, type Schemas } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { EmptyState } from '../../../components/States';
import { Panel } from '../../../components/Panel';
import { fileNameOf, saveFile } from '../../../lib/files';
import { notifyDone, notifyFailed, notifyNote } from '../../../lib/notify';
import { SettingsPage } from './shared';

type Account = Schemas['MailAccountOut'];
type AccountIn = Schemas['MailAccountIn'];

const mailQuery = {
  queryKey: ['admin', 'settings', 'mail'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/mail')),
};

const EMPTY: AccountIn = {
  key: '',
  host: '',
  port: 587,
  username: '',
  password: null,
  starttls: true,
  from_email: null,
  from_name: null,
};

function useMailChange<Input, Output>(
  run: (input: Input) => Promise<Output>,
  done: (output: Output) => void,
) {
  const client = useQueryClient();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const mutation = useMutation({
    mutationFn: run,
    onSuccess: async (output) => {
      setErrors({});
      done(output);
      // The accounts, and the senders the other sections offer.
      await client.invalidateQueries({ queryKey: ['admin', 'settings'] });
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  return { mutation, errors, setErrors };
}

/** Adding an account, or changing one. */
function AccountForm({ editing, onDone }: { editing: Account | null; onDone: () => void }) {
  const [value, setValue] = useState<AccountIn>(
    editing
      ? {
          key: editing.key,
          host: editing.host,
          port: editing.port,
          username: editing.username,
          password: null,
          starttls: editing.starttls,
          from_email: editing.from_email,
          from_name: editing.from_name,
        }
      : EMPTY,
  );
  const { mutation, errors, setErrors } = useMailChange(
    (body: AccountIn) =>
      editing
        ? call(
            api.PUT('/api/v1/admin/settings/mail/accounts/{account_id}', {
              params: { path: { account_id: editing.id } },
              body,
            }),
          )
        : call(api.POST('/api/v1/admin/settings/mail/accounts', { body })),
    () => {
      notifyDone(editing ? 'Saved.' : 'Added.');
      setValue(EMPTY);
      onDone();
    },
  );
  const set = <K extends keyof AccountIn>(key: K, next: AccountIn[K]) => {
    setValue({ ...value, [key]: next });
    if (errors[key]) setErrors(Object.fromEntries(Object.entries(errors).filter(([field]) => field !== key)));
  };
  const text = (key: 'key' | 'host' | 'username' | 'from_email' | 'from_name', label: string, extra = {}) => (
    <TextInput
      label={label}
      // The help under the field, so fields side by side line up.
      inputWrapperOrder={['label', 'input', 'description', 'error']}
      {...extra}
      value={value[key] ?? ''}
      error={errors[key] ?? null}
      onChange={(event) => {
        set(key, event.currentTarget.value);
      }}
    />
  );

  return (
    <Panel
      title={editing ? `Change ${editing.key}` : 'Add an account'}
      actions={
        editing ? (
          <Button size="xs" variant="default" onClick={onDone}>
            Cancel
          </Button>
        ) : null
      }
    >
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
          {text('key', 'Key', {
            placeholder: 'office',
            description: 'How it is chosen elsewhere: letters, digits, - and _.',
          })}
          {text('host', 'SMTP server', { placeholder: 'smtp.example.com' })}
          <NumberInput
            label="Port"
            min={1}
            max={65535}
            allowDecimal={false}
            value={value.port}
            error={errors.port ?? null}
            onChange={(port) => {
              set('port', typeof port === 'number' ? port : 0);
            }}
          />
        </SimpleGrid>
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          {text('username', 'Username', { placeholder: 'noreply@example.com' })}
          <PasswordInput
            label="Password"
            description={editing ? 'Leave empty to keep the one it has.' : undefined}
            autoComplete="new-password"
            value={value.password ?? ''}
            error={errors.password ?? null}
            onChange={(event) => {
              set('password', event.currentTarget.value || null);
            }}
          />
          {text('from_email', 'Sends as (optional)', {
            placeholder: 'Same as the username',
            description: 'Where the login is not an address, such as Brevo: the address emails come from.',
          })}
          {text('from_name', 'Sender name (optional)', { placeholder: 'Joanneum Aeronautics' })}
        </SimpleGrid>
        <Checkbox
          label="STARTTLS"
          description="On a port such as 587. Off: SSL/TLS from the start, such as on 465."
          checked={value.starttls ?? true}
          onChange={(event) => {
            set('starttls', event.currentTarget.checked);
          }}
        />
        <Group justify="flex-end">
          <Button
            loading={mutation.isPending}
            onClick={() => {
              mutation.mutate(value);
            }}
          >
            {editing ? 'Save' : 'Add'}
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

function AccountRow({ account, onEdit }: { account: Account; onEdit: () => void }) {
  const client = useQueryClient();
  const test = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/admin/settings/mail/accounts/{account_id}/test', {
          params: { path: { account_id: account.id } },
        }),
      ),
    onSuccess: (result) => {
      if (result.ok) notifyDone(`${account.key}: ${result.message}`);
      else notifyFailed(new ApiError(200, 'mail_test_failed', `${account.key}: ${result.message}`));
    },
    onError: notifyFailed,
  });
  const remove = useMutation({
    mutationFn: () =>
      call(
        api.DELETE('/api/v1/admin/settings/mail/accounts/{account_id}', {
          params: { path: { account_id: account.id } },
        }),
      ),
    onSuccess: async (result) => {
      notifyDone(`${account.key} removed.`);
      if (result.removed_welcome_sender)
        notifyNote('It sent the welcome email: none is sent until another sender is chosen under General.');
      await client.invalidateQueries({ queryKey: ['admin', 'settings'] });
    },
    onError: notifyFailed,
  });

  return (
    <Table.Tr>
      <Table.Td fw={600}>{account.key}</Table.Td>
      <Table.Td>
        {account.host}:{account.port}
        <Text size="xs" c="dimmed">
          {account.starttls ? 'STARTTLS' : 'SSL/TLS'}
        </Text>
      </Table.Td>
      <Table.Td>
        {account.username}
        {account.from_email ? (
          <Text size="xs" c="dimmed">
            sends as {account.from_name ? `${account.from_name} <${account.from_email}>` : account.from_email}
          </Text>
        ) : null}
      </Table.Td>
      <Table.Td>
        <Group gap="xs" justify="flex-end" wrap="nowrap">
          <Button size="xs" variant="default" onClick={onEdit}>
            Change
          </Button>
          <Button
            size="xs"
            variant="default"
            loading={test.isPending}
            onClick={() => {
              test.mutate();
            }}
          >
            Test
          </Button>
          <ConfirmButton
            size="xs"
            confirmLabel="Yes, remove"
            loading={remove.isPending}
            onConfirm={() => {
              remove.mutate();
            }}
          >
            Remove
          </ConfirmButton>
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}

function ImportExport() {
  const [file, setFile] = useState<File | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [password, setPassword] = useState('');
  const imported = useMailChange(
    async () => {
      if (!file) throw new Error('Choose a file.');
      const form = new FormData();
      form.append('file', file);
      return call(
        api.POST('/api/v1/admin/settings/mail/import', {
          params: { query: { overwrite } },
          // openapi-fetch sends a FormData as it is, with its own Content-Type.
          body: form as unknown as { file: string },
        }),
      );
    },
    (result) => {
      setFile(null);
      if (result.created || result.updated)
        notifyDone(`Imported: ${String(result.created)} added, ${String(result.updated)} replaced.`);
      else notifyNote('Nothing imported.');
      if (result.skipped.length) notifyNote(`Kept as they were: ${result.skipped.join(', ')}.`);
    },
  );
  const exported = useMailChange(
    async () => {
      const { data, error, response } = await api.POST('/api/v1/admin/settings/mail/export', {
        body: { password },
        parseAs: 'blob',
      });
      if (!response.ok || !(data instanceof Blob)) {
        await call(Promise.resolve({ data: undefined, error, response }));
        throw new ApiError(response.status, 'unexpected', 'The export could not be made just now.');
      }
      return { file: data, name: fileNameOf(response, 'mail-accounts.json') };
    },
    (result) => {
      setPassword('');
      saveFile(result.file, result.name);
    },
  );

  return (
    <Panel title="Import and export">
      <Stack gap="lg">
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            SMTP providers have no common export, so a JSON file: this portal&apos;s export, the older
            MAIL_ACCOUNTS_JSON mapping, or a list of accounts with the usual SMTP keys.
          </Text>
          <Group align="flex-end" gap="md">
            <FileInput
              label="JSON file"
              placeholder="Choose a .json file"
              accept=".json,application/json"
              clearable
              value={file}
              error={imported.errors.file ?? null}
              onChange={(next) => {
                setFile(next);
                imported.setErrors({});
              }}
              flex="1 1 16rem"
            />
            <Checkbox
              label="Replace accounts with the same key"
              checked={overwrite}
              onChange={(event) => {
                setOverwrite(event.currentTarget.checked);
              }}
              mb={8}
            />
            <Button
              loading={imported.mutation.isPending}
              disabled={!file}
              onClick={() => {
                imported.mutation.mutate(undefined);
              }}
            >
              Import
            </Button>
          </Group>
        </Stack>
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            The export has every password in it, readable: export only when needed, and keep the file safe.
          </Text>
          <Group align="flex-end" gap="md">
            <PasswordInput
              label="Your current password"
              autoComplete="current-password"
              value={password}
              error={exported.errors.password ?? null}
              onChange={(event) => {
                setPassword(event.currentTarget.value);
                exported.setErrors({});
              }}
              flex="1 1 16rem"
            />
            <Button
              variant="default"
              loading={exported.mutation.isPending}
              disabled={!password}
              onClick={() => {
                exported.mutation.mutate(undefined);
              }}
            >
              Export
            </Button>
          </Group>
        </Stack>
      </Stack>
    </Panel>
  );
}

function Accounts({ data }: { data: Schemas['MailOut'] }) {
  const [editing, setEditing] = useState<Account | null>(null);
  return (
    <Stack gap="lg">
      <Panel title="Accounts" flush>
        {data.accounts.length ? (
          <Table.ScrollContainer minWidth={680} type="native">
            <Table verticalSpacing="sm" aria-label="Mail accounts">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Key</Table.Th>
                  <Table.Th scope="col">Server</Table.Th>
                  <Table.Th scope="col">Username</Table.Th>
                  <Table.Th scope="col">
                    <VisuallyHidden>Actions</VisuallyHidden>
                  </Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.accounts.map((account) => (
                  <AccountRow
                    key={account.id}
                    account={account}
                    onEdit={() => {
                      setEditing(account);
                    }}
                  />
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ) : (
          <Stack px="md">
            <EmptyState>None yet: no email can be sent until there is one.</EmptyState>
          </Stack>
        )}
      </Panel>
      <AccountForm
        key={editing ? editing.id : 'new'}
        editing={editing}
        onDone={() => {
          setEditing(null);
        }}
      />
      <ImportExport />
    </Stack>
  );
}

export function MailAccounts() {
  return (
    <SettingsPage
      title="Mail accounts"
      description="The accounts the portal sends email from."
      query={mailQuery}
    >
      {(data) => <Accounts data={data} />}
    </SettingsPage>
  );
}
