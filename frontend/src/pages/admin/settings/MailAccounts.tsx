/**
 * Settings › Email, *Accounts*: the accounts the portal sends email from --
 * SMTP servers and Microsoft 365 mailboxes, side by side, added, changed,
 * tested, removed -- the connection to Microsoft those sign in with (its app
 * registration and the certificate the portal makes), and importing or
 * exporting the accounts as a JSON file. SMTP passwords never reach the
 * browser, except in an export, which asks for the current password first.
 * Data: /api/v1/admin/settings/mail (aeronautics_members/api/admin_mail.py;
 * docs/microsoft-mail-plan.md).
 */
import {
  Button,
  Checkbox,
  FileInput,
  Group,
  NumberInput,
  PasswordInput,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  VisuallyHidden,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { IconBrandWindows, IconMail, IconPencil, IconTransfer } from '@tabler/icons-react';

import { api, ApiError, call, type Schemas } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { EmptyState } from '../../../components/States';
import { Panel } from '../../../components/Panel';
import { fileNameOf, saveFile } from '../../../lib/files';
import { formatDayOf } from '../../../lib/format';
import { notifyDone, notifyFailed, notifyNote } from '../../../lib/notify';
import { SettingsLoaded, useNotMyLogin } from './shared';

type Account = Schemas['MailAccountOut'];
type AccountIn = Schemas['MailAccountIn'];
type Microsoft = Schemas['MicrosoftOut'];

const mailQuery = {
  queryKey: ['admin', 'settings', 'mail'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/mail')),
};

const EMPTY: AccountIn = {
  key: '',
  kind: 'smtp',
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
          kind: editing.kind,
          host: editing.host ?? '',
          port: editing.port ?? 587,
          username: editing.username ?? '',
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
  const notLogin = useNotMyLogin();
  const notPassword = useNotMyLogin(true);
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

  const microsoft = value.kind === 'microsoft';

  return (
    <Panel
      icon={IconPencil}
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
        <SegmentedControl
          aria-label="Kind of account"
          w="fit-content"
          data={[
            { value: 'smtp', label: 'SMTP' },
            { value: 'microsoft', label: 'Microsoft 365' },
          ]}
          value={value.kind ?? 'smtp'}
          onChange={(kind) => {
            set('kind', kind as AccountIn['kind']);
          }}
        />
        {microsoft ? (
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
            {text('key', 'Key', {
              placeholder: 'support',
              description: 'How it is chosen elsewhere: letters, digits, - and _.',
            })}
            {text('from_email', 'Mailbox', {
              placeholder: 'support@example.org',
              description: 'A shared mailbox the app may use (Microsoft 365 below).',
            })}
            {text('from_name', 'Sender name (optional)', { placeholder: 'Joanneum Aeronautics' })}
          </SimpleGrid>
        ) : (
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
                value={value.port ?? 587}
                error={errors.port ?? null}
                onChange={(port) => {
                  set('port', typeof port === 'number' ? port : 0);
                }}
              />
            </SimpleGrid>
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              {text('username', 'Username', {
                placeholder: 'noreply@example.com',
                name: 'smtp-login',
                ...notLogin,
              })}
              <PasswordInput
                label="Password"
                description={editing ? 'Leave empty to keep the one it has.' : undefined}
                name="smtp-secret"
                {...notPassword}
                value={value.password ?? ''}
                error={errors.password ?? null}
                onChange={(event) => {
                  set('password', event.currentTarget.value || null);
                }}
              />
              {text('from_email', 'Sends as (optional)', {
                placeholder: 'Same as the username',
                description:
                  'Where the login is not an address, such as Brevo: the address emails come from.',
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
          </Stack>
        )}
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
      if (result.cleared_senders.length)
        notifyNote('What it sent goes out from the default account now (the Senders tab).');
      await client.invalidateQueries({ queryKey: ['admin', 'settings'] });
    },
    onError: notifyFailed,
  });

  return (
    <Table.Tr>
      <Table.Td fw={600}>{account.key}</Table.Td>
      {account.kind === 'microsoft' ? (
        <>
          <Table.Td>
            Microsoft 365
            <Text size="xs" c="dimmed">
              Graph
            </Text>
          </Table.Td>
          <Table.Td>
            {account.from_email}
            {account.from_name ? (
              <Text size="xs" c="dimmed">
                as {account.from_name}
              </Text>
            ) : null}
          </Table.Td>
        </>
      ) : (
        <>
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
                sends as{' '}
                {account.from_name ? `${account.from_name} <${account.from_email}>` : account.from_email}
              </Text>
            ) : null}
          </Table.Td>
        </>
      )}
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

/** One certificate: its thumbprint as Entra ID shows it, how long it is valid, and its file. */
function CertificateLine({
  label,
  certificate,
  which,
  extra,
}: {
  label: string;
  certificate: NonNullable<Microsoft['certificate']>;
  which: 'current' | 'new';
  extra?: React.ReactNode;
}) {
  const download = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.GET('/api/v1/admin/settings/mail/microsoft/certificate', {
        params: { query: { which } },
        parseAs: 'blob',
      });
      if (!response.ok || !(data instanceof Blob)) {
        await call(Promise.resolve({ data: undefined, error, response }));
        throw new ApiError(response.status, 'unexpected', 'The certificate could not be fetched just now.');
      }
      saveFile(data, fileNameOf(response, 'portal-mail.cer'));
    },
    onError: notifyFailed,
  });
  return (
    <Group justify="space-between" align="flex-start" gap="sm" wrap="wrap">
      <Stack gap={2}>
        <Text size="sm" fw={600}>
          {label}
        </Text>
        <Text size="xs" c="dimmed" ff="monospace" style={{ wordBreak: 'break-all' }}>
          {certificate.thumbprint}
        </Text>
        <Text size="xs" c="dimmed">
          Valid until {formatDayOf(certificate.expires_at)}
        </Text>
      </Stack>
      <Group gap="xs">
        <Button
          size="xs"
          variant="default"
          loading={download.isPending}
          onClick={() => {
            download.mutate();
          }}
        >
          Download
        </Button>
        {extra}
      </Group>
    </Group>
  );
}

/** Microsoft 365: the app registration the Microsoft accounts sign in with, and its certificate. */
function MicrosoftConnection({ microsoft }: { microsoft: Microsoft }) {
  const [app, setApp] = useState({
    tenant_id: microsoft.tenant_id ?? '',
    client_id: microsoft.client_id ?? '',
  });
  const saved = useMailChange(
    () => call(api.PUT('/api/v1/admin/settings/mail/microsoft', { body: app })),
    () => {
      notifyDone('Saved.');
    },
  );
  const made = useMailChange(
    () => call(api.POST('/api/v1/admin/settings/mail/microsoft/certificate')),
    () => {
      notifyDone('Made. Download it and upload it to the app registration.');
    },
  );
  const dropped = useMailChange(
    () => call(api.DELETE('/api/v1/admin/settings/mail/microsoft/certificate/new')),
    () => {
      notifyDone('Dropped.');
    },
  );
  const checked = useMailChange(
    () => call(api.POST('/api/v1/admin/settings/mail/microsoft/check')),
    (result) => {
      if (result.ok) notifyDone(result.message);
      else notifyFailed(new ApiError(200, 'microsoft_check_failed', result.message));
    },
  );
  // IDs, not a sign-in: the browser's saved login must not land here.
  const notLogin = useNotMyLogin();
  const field = (key: 'tenant_id' | 'client_id', label: string, placeholder: string) => (
    <TextInput
      label={label}
      placeholder={placeholder}
      name={`microsoft-${key}`}
      {...notLogin}
      value={app[key]}
      error={saved.errors[key] ?? null}
      onChange={(event) => {
        setApp({ ...app, [key]: event.currentTarget.value });
        saved.setErrors({});
      }}
    />
  );

  return (
    <Panel icon={IconBrandWindows} title="Microsoft 365">
      <Stack gap="lg">
        <Text size="sm" c="dimmed">
          Microsoft 365 accounts sign in as the portal&apos;s app in Entra ID, with a certificate made here:
          its key never leaves the server. Setting it up: docs/microsoft-mail-setup.md.
        </Text>
        <Group align="flex-end" gap="md">
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md" flex="1 1 24rem">
            {field('tenant_id', 'Directory (tenant) ID', 'example.onmicrosoft.com')}
            {field('client_id', 'Application (client) ID', '00000000-0000-0000-0000-000000000000')}
          </SimpleGrid>
          <Button
            loading={saved.mutation.isPending}
            onClick={() => {
              saved.mutation.mutate(undefined);
            }}
          >
            Save
          </Button>
        </Group>
        <Stack gap="md">
          {microsoft.certificate ? (
            <CertificateLine label="Certificate in use" certificate={microsoft.certificate} which="current" />
          ) : null}
          {microsoft.new_certificate ? (
            <CertificateLine
              label="New certificate, used once it signs in"
              certificate={microsoft.new_certificate}
              which="new"
              extra={
                <ConfirmButton
                  size="xs"
                  confirmLabel="Yes, drop it"
                  loading={dropped.mutation.isPending}
                  onConfirm={() => {
                    dropped.mutation.mutate(undefined);
                  }}
                >
                  Drop
                </ConfirmButton>
              }
            />
          ) : null}
          <Group gap="sm">
            {microsoft.new_certificate ? null : (
              <Button
                variant={microsoft.certificate ? 'default' : 'filled'}
                loading={made.mutation.isPending}
                onClick={() => {
                  made.mutation.mutate(undefined);
                }}
              >
                {microsoft.certificate ? 'Make a new certificate' : 'Make a certificate'}
              </Button>
            )}
            <Button
              variant="default"
              disabled={!microsoft.certificate && !microsoft.new_certificate}
              loading={checked.mutation.isPending}
              onClick={() => {
                checked.mutation.mutate(undefined);
              }}
            >
              Check sign-in
            </Button>
          </Group>
        </Stack>
      </Stack>
    </Panel>
  );
}

function ImportExport() {
  const [file, setFile] = useState<File | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [password, setPassword] = useState('');
  // Asked for on purpose: filled in by the browser, asking would prove nothing.
  const notFilledIn = useNotMyLogin(true);
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
    <Panel icon={IconTransfer} title="Import and export">
      <Stack gap="lg">
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            SMTP providers have no common export, so a JSON file: this portal&apos;s export, the older
            MAIL_ACCOUNTS_JSON mapping, or a list of accounts with the usual SMTP keys. Microsoft 365 accounts
            go with it; their connection does not.
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
            The export has every SMTP password in it, readable: export only when needed, and keep the file
            safe.
          </Text>
          <Group align="flex-end" gap="md">
            <PasswordInput
              label="Your current password"
              name="export-confirm"
              {...notFilledIn}
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
      <Panel icon={IconMail} title="Accounts" flush>
        {data.accounts.length ? (
          <Table.ScrollContainer minWidth={680} type="native">
            <Table verticalSpacing="sm" aria-label="Mail accounts">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Key</Table.Th>
                  <Table.Th scope="col">Server</Table.Th>
                  <Table.Th scope="col">Login or mailbox</Table.Th>
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
      <MicrosoftConnection
        key={`${data.microsoft.tenant_id ?? ''}|${data.microsoft.client_id ?? ''}`}
        microsoft={data.microsoft}
      />
      <ImportExport />
    </Stack>
  );
}

/** Settings › Email, *Accounts*: the accounts the portal sends email from. */
export function MailAccountsTab() {
  return <SettingsLoaded query={mailQuery}>{(data) => <Accounts data={data} />}</SettingsLoaded>;
}
