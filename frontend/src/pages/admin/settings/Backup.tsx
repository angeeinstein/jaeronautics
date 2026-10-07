/**
 * Settings -> Backup and restore: one encrypted file with everything this
 * installation holds, made in the background while the page follows along;
 * the backups kept on the server; how to restore one. On a portal just
 * restored, first its paused background jobs -- resumed only once somebody
 * says this is the server members use -- and then, line by line, whether
 * everything works. Data: /api/v1/admin/settings/backup
 * (aeronautics_members/api/admin_backup.py); a backup is downloaded from
 * /admin/backup/files/<name>.
 */
import {
  Alert,
  Button,
  Checkbox,
  Code,
  Collapse,
  Group,
  PasswordInput,
  SimpleGrid,
  Stack,
  Table,
  Text,
  VisuallyHidden,
} from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type SyntheticEvent, useState } from 'react';

import { api, ApiError, call, type Schemas } from '../../../api/client';
import { Checklist } from '../../../components/Checklist';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { ErrorState, LoadingState } from '../../../components/States';
import { formatDateTime } from '../../../lib/format';
import { notifyDone, notifyFailed } from '../../../lib/notify';
import classes from './Maintenance.module.css';

type Page = Schemas['BackupOut'];
type Run = Schemas['BackupRunOut'];

const KEY = ['admin', 'settings', 'backup'] as const;
const CHECKLIST_KEY = ['admin', 'settings', 'backup', 'checklist'] as const;

function usePage() {
  return useQuery({
    queryKey: KEY,
    queryFn: () => call(api.GET('/api/v1/admin/settings/backup')),
    // While a backup is being made, its steps as they happen.
    refetchInterval: (query) => (query.state.data?.run?.state === 'running' ? 1500 : false),
  });
}

function Paused({ page }: { page: Page }) {
  const client = useQueryClient();
  const [sure, setSure] = useState(false);
  const resume = useMutation({
    mutationFn: () => call(api.POST('/api/v1/admin/settings/backup/resume', { body: { confirm: true } })),
    onSuccess: (fresh) => {
      client.setQueryData(KEY, fresh);
      notifyDone('Background jobs resumed.');
    },
    onError: notifyFailed,
  });
  const made = page.paused?.backup_created_at;
  return (
    <Panel title="Background jobs" actions={<Pill tone="pending">Paused</Pill>}>
      <Stack gap="md">
        <Alert color="amber" variant="light">
          This portal was restored from a backup{made ? ` made on ${formatDateTime(made)}` : ''}. Its
          background jobs are paused: it sends no emails, does not sync the forum and does not check payments
          on its own, so a copy on a test machine cannot reach members or the live forum.
        </Alert>
        <Checklist label="Background timers" items={page.jobs} />
        <Checkbox
          label="This is the server members use. Send emails, sync the forum and check payments from here."
          checked={sure}
          onChange={(event) => {
            setSure(event.currentTarget.checked);
          }}
        />
        <Group>
          <Button
            disabled={!sure}
            loading={resume.isPending}
            onClick={() => {
              resume.mutate();
            }}
          >
            Resume background jobs
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

/** After resuming: each poll runs the next check not done yet; then, slowly, for the timers. */
function Working({ first }: { first: Schemas['CheckOut'][] }) {
  const client = useQueryClient();
  const checklist = useQuery({
    queryKey: CHECKLIST_KEY,
    queryFn: () => call(api.GET('/api/v1/admin/settings/backup/checklist')),
    refetchInterval: (query) => (query.state.data?.done ? 30_000 : 2500),
  });
  const again = useMutation({
    mutationFn: () => call(api.POST('/api/v1/admin/settings/backup/checklist/again')),
    onSuccess: async (fresh) => {
      client.setQueryData(CHECKLIST_KEY, fresh);
      await checklist.refetch();
    },
    onError: notifyFailed,
  });
  const dismiss = useMutation({
    mutationFn: () => call(api.POST('/api/v1/admin/settings/backup/checklist/dismiss')),
    onSuccess: () => client.invalidateQueries({ queryKey: KEY }),
    onError: notifyFailed,
  });
  return (
    <Panel
      title="Is everything working?"
      actions={
        <>
          <Button
            size="xs"
            variant="default"
            loading={again.isPending}
            onClick={() => {
              again.mutate();
            }}
          >
            Check again
          </Button>
          <Button
            size="xs"
            variant="default"
            loading={dismiss.isPending}
            onClick={() => {
              dismiss.mutate();
            }}
          >
            Hide this list
          </Button>
        </>
      }
    >
      <Checklist label="Is everything working?" items={checklist.data?.items ?? first} />
    </Panel>
  );
}

const STEP: Record<Schemas['StepOut']['state'], 'ok' | 'running' | 'failed' | 'pending'> = {
  done: 'ok',
  running: 'running',
  failed: 'failed',
  pending: 'pending',
};

function Progress({ run }: { run: Run }) {
  const [showLog, setShowLog] = useState(run.state === 'running');
  return (
    <Stack gap="sm">
      <Checklist
        label="Making the backup"
        items={run.steps.map((step) => ({ key: step.key, label: step.label, state: STEP[step.state] }))}
      />
      {run.state === 'completed' && run.result ? (
        <Alert color="green" variant="light">
          {`Last backup: ${run.result.file}, ${String(run.result.rows)} rows and ${String(run.result.files)} files, checked after writing.`}
        </Alert>
      ) : run.state === 'failed' ? (
        <Alert color="red" variant="light" role="alert">
          {run.error ?? 'The backup failed.'}
        </Alert>
      ) : null}
      {run.log.length ? (
        <div>
          <Button
            size="xs"
            variant="subtle"
            aria-expanded={showLog}
            onClick={() => {
              setShowLog(!showLog);
            }}
          >
            {showLog ? 'Hide the progress log' : 'Show the progress log'}
          </Button>
          <Collapse expanded={showLog}>
            <Code block className={classes.log}>
              {run.log.join('\n')}
            </Code>
          </Collapse>
        </div>
      ) : null}
    </Stack>
  );
}

function MakeOne({ page }: { page: Page }) {
  const client = useQueryClient();
  const [passphrase, setPassphrase] = useState('');
  const [again, setAgain] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const running = page.run?.state === 'running';
  const start = useMutation({
    mutationFn: () =>
      call(api.POST('/api/v1/admin/settings/backup', { body: { passphrase, passphrase_again: again } })),
    onSuccess: (fresh) => {
      setPassphrase('');
      setAgain('');
      client.setQueryData(KEY, fresh);
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    start.mutate();
  };
  return (
    <Panel title="Make a backup">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          One file with everything this installation holds: all accounts and memberships, every setting and
          credential, pictures waiting for review, and the secret key, Stripe keys and mail accounts from the
          server configuration. Restored onto a fresh install, it gives back this portal as it is now.
        </Text>
        <Alert color="brand" variant="light">
          The file is encrypted with the passphrase you choose here. The passphrase is not stored anywhere:
          without it the backup cannot be restored, by anyone. Keep it somewhere safe, apart from the file.
        </Alert>
        {page.run ? <Progress key={page.run.started_at ?? ''} run={page.run} /> : null}
        <form onSubmit={submit}>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <PasswordInput
              label="Passphrase"
              placeholder={`At least ${String(page.min_passphrase_length)} characters`}
              autoComplete="new-password"
              value={passphrase}
              error={errors.passphrase ?? null}
              onChange={(event) => {
                setPassphrase(event.currentTarget.value);
                setErrors({});
              }}
            />
            <PasswordInput
              label="Passphrase again"
              autoComplete="new-password"
              value={again}
              error={errors.passphrase_again ?? null}
              onChange={(event) => {
                setAgain(event.currentTarget.value);
                setErrors({});
              }}
            />
          </SimpleGrid>
          <Group justify="flex-end" mt="md">
            <Button type="submit" loading={start.isPending} disabled={running || !passphrase || !again}>
              Back up
            </Button>
          </Group>
        </form>
      </Stack>
    </Panel>
  );
}

function Kept({ page }: { page: Page }) {
  const client = useQueryClient();
  const remove = useMutation({
    mutationFn: (name: string) =>
      call(api.DELETE('/api/v1/admin/settings/backup/files/{name}', { params: { path: { name } } })),
    onSuccess: async ({ name }) => {
      notifyDone(`Deleted ${name}.`);
      await client.invalidateQueries({ queryKey: KEY });
    },
    onError: notifyFailed,
  });
  return (
    <Panel title="Backups on this server" flush>
      <Table.ScrollContainer minWidth={600} type="native">
        <Table verticalSpacing="sm" aria-label="Backups on this server">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Backup</Table.Th>
              <Table.Th scope="col">Made</Table.Th>
              <Table.Th scope="col">Size</Table.Th>
              <Table.Th scope="col">
                <VisuallyHidden>Actions</VisuallyHidden>
              </Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {page.backups.map((file) => (
              <Table.Tr key={file.name}>
                <Table.Td className={classes.name}>{file.name}</Table.Td>
                <Table.Td>{formatDateTime(file.made_at)}</Table.Td>
                <Table.Td>{file.size_display}</Table.Td>
                <Table.Td>
                  <Group gap="xs" justify="flex-end" wrap="nowrap">
                    {/* A download from Flask, which logs it; not a page of the app. */}
                    <Button
                      component="a"
                      href={`/admin/backup/files/${encodeURIComponent(file.name)}`}
                      size="xs"
                      variant="default"
                    >
                      Download
                    </Button>
                    <ConfirmButton
                      size="xs"
                      confirmLabel="Yes, delete"
                      loading={remove.isPending && remove.variables === file.name}
                      onConfirm={() => {
                        remove.mutate(file.name);
                      }}
                    >
                      Delete
                    </ConfirmButton>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Text size="sm" c="dimmed" px="md" py="sm">
        {`The newest ${String(page.kept)} backups are kept on the server; older ones are removed when a new one is made. Download a backup to keep it somewhere else.`}
      </Text>
    </Panel>
  );
}

function Restore({ page }: { page: Page }) {
  return (
    <Panel title="Restore">
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          Restoring replaces everything on an installation with the backup, including any account made while
          installing. Copy the file to the server and run the installer with it; this works on a fresh install
          and on an existing one:
        </Text>
        <Code block className={classes.log}>
          {`sudo ./install.sh --restore /path/to/${page.backups[0]?.name ?? 'portal-backup.jabackup'}`}
        </Code>
        <Text size="sm" c="dimmed">
          It asks for the passphrase, restores, restarts the portal and leaves every background job paused
          until you resume them here.
        </Text>
      </Stack>
    </Panel>
  );
}

export function Backup() {
  const page = usePage();
  return (
    <>
      <PageHeader
        title="Backup and restore"
        description="One file with everything, and how to bring it back."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Settings' }, { label: 'Backup and restore' }]}
      />
      {page.isPending ? (
        <LoadingState />
      ) : page.isError ? (
        <ErrorState error={page.error} onRetry={() => void page.refetch()} />
      ) : (
        <Stack gap="lg">
          {page.data.paused ? <Paused page={page.data} /> : null}
          {!page.data.paused && page.data.checklist ? <Working first={page.data.checklist} /> : null}
          <MakeOne page={page.data} />
          {page.data.backups.length ? <Kept page={page.data} /> : null}
          <Restore page={page.data} />
        </Stack>
      )}
    </>
  );
}
