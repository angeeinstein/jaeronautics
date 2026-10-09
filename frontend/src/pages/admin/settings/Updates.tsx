/**
 * Settings -> Updates: the version running and the newest one, installing
 * it, going back to the one before, and how the last update went -- its steps
 * ticked off as they happen, and below them, folded away, everything the
 * installer printed, as in a terminal.
 *
 * A newer version is offered once CI has passed for it: the installer takes
 * CI's build and would otherwise sit waiting for it. While CI runs the page
 * says so, with about how long it usually takes, and looks again every
 * minute; a version whose CI failed is not offered.
 *
 * An update restarts the very server that answers this page, so while one
 * runs the page asks again every two seconds, takes a failed answer for the
 * restart it is, and once the update is done loads itself again -- with the
 * new version's front end. Beside the button: whether anybody else is using
 * the portal right now (Presence.tsx). Data: GET|POST
 * /api/v1/admin/settings/updates (aeronautics_members/api/admin_system.py).
 */
import { Alert, Anchor, Button, Code, Collapse, Group, Loader, Progress, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { api, call, type Schemas } from '../../../api/client';
import { type CheckState, Checklist } from '../../../components/Checklist';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { type Detail, Details } from '../../../components/Details';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { ErrorState, LoadingState } from '../../../components/States';
import { formatDateTime, plural } from '../../../lib/format';
import { notifyFailed } from '../../../lib/notify';
import { reloadPage } from '../../../lib/reload';
import classes from './Maintenance.module.css';
import { PresenceNote } from './Presence';

type State = Schemas['UpdatesOut'];
type Action = Schemas['UpdateIn']['action'];

const KEY = ['admin', 'settings', 'updates'] as const;
const POLL_MS = 2000;
// While CI checks the newest version: the server asks GitHub at most every 90 s.
const CI_POLL_MS = 60_000;
const RELOAD_AFTER_MS = 1500;

const STEP: Record<Schemas['UpdateStepOut']['state'], CheckState> = {
  done: 'ok',
  running: 'running',
  failed: 'failed',
  pending: 'pending',
};

function Steps({ steps, label }: { steps: Schemas['UpdateStepOut'][]; label: string }) {
  if (!steps.length) return null;
  return (
    <Checklist
      label={label}
      items={steps.map((step, index) => ({
        key: `${String(index)}:${step.label}`,
        label: step.label,
        state: STEP[step.state],
        detail: step.detail,
      }))}
    />
  );
}

function Log({ text }: { text: string }) {
  const node = useRef<HTMLPreElement>(null);
  // Following the newest output -- unless somebody scrolled up to read.
  const following = useRef(true);
  useEffect(() => {
    if (node.current && following.current) node.current.scrollTop = node.current.scrollHeight;
  }, [text]);
  return (
    <Code
      block
      ref={node}
      className={classes.log}
      onScroll={(event) => {
        const box = event.currentTarget;
        following.current = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
      }}
    >
      {text}
    </Code>
  );
}

/** Everything the update printed, folded away; fetched when opened, and again while one runs. */
function Output({ running, fallback }: { running: boolean; fallback: string | null }) {
  const [open, setOpen] = useState(false);
  const output = useQuery({
    queryKey: [...KEY, 'log'],
    queryFn: () => call(api.GET('/api/v1/admin/settings/updates/log')),
    enabled: open,
    refetchInterval: running ? POLL_MS : false,
    refetchIntervalInBackground: true,
  });
  // While the site restarts the output cannot be fetched: what came last stays.
  const text = output.data?.text ?? fallback;
  return (
    <div>
      <Button
        size="xs"
        variant="subtle"
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
        }}
      >
        {open ? 'Hide the terminal output' : 'Show the terminal output'}
      </Button>
      <Collapse expanded={open}>
        <Stack gap={4}>
          {output.data?.cut ? (
            <Text size="xs" c="dimmed">
              The output is very long; this is its end.
            </Text>
          ) : null}
          {open && output.isPending ? (
            <LoadingState />
          ) : (
            <Log text={text ?? (running ? 'Waiting for output…' : 'No output.')} />
          )}
        </Stack>
      </Collapse>
    </div>
  );
}

function Version({ state, onCheck, checking }: { state: State; onCheck: () => void; checking: boolean }) {
  const installed = state.installed;
  return (
    <Panel
      title="Version"
      actions={
        <Button size="xs" variant="default" loading={checking} onClick={onCheck}>
          Check again
        </Button>
      }
    >
      <Details
        items={[
          [
            'Installed',
            <Stack key="installed" gap={2}>
              <Group gap="xs">
                <Code>{installed.short_revision ?? 'unknown'}</Code>
                {/* After a rollback no branch is checked out: say what happened instead. */}
                {installed.rolled_back ? (
                  <Pill tone="pending">Rolled back</Pill>
                ) : installed.branch ? (
                  <Text size="sm" c="dimmed">
                    ({installed.branch})
                  </Text>
                ) : null}
              </Group>
              {installed.committed_at ? (
                <Text size="xs" c="dimmed">
                  {formatDateTime(installed.committed_at)}
                </Text>
              ) : null}
              {installed.subject ? (
                <Text size="xs" c="dimmed">
                  {installed.subject}
                </Text>
              ) : null}
            </Stack>,
          ],
          [
            'Latest available',
            state.latest_check_failed ? (
              <Text size="sm" c="dimmed">
                Could not be checked right now.
              </Text>
            ) : (
              <Group gap="xs">
                <Code>{state.latest ?? 'unknown'}</Code>
                {state.latest_check?.state === 'passed' ? <Pill tone="active">Checks passed</Pill> : null}
                {state.latest_check?.state === 'running' ? <Pill tone="pending">Being checked</Pill> : null}
                {state.latest_check?.state === 'failed' ? <Pill tone="failed">Checks failed</Pill> : null}
              </Group>
            ),
          ],
        ]}
      />
    </Panel>
  );
}

function Running({ state, phase }: { state: State; phase: 'running' | 'restarting' | 'finished' }) {
  const { percent, steps, steps_done: done, steps_expected: expected } = state.progress;
  return (
    <Stack gap="md">
      <Group justify="space-between" gap="sm">
        <Text fw={600} role="status" aria-live="polite">
          {phase === 'finished'
            ? 'Finished. Loading the new version…'
            : phase === 'restarting'
              ? 'The site is restarting…'
              : 'Update running'}
        </Text>
        {expected && phase !== 'finished' ? (
          <Text size="sm" c="dimmed">
            {`${String(Math.min(done, expected))} of ${String(expected)} steps`}
          </Text>
        ) : null}
      </Group>
      {/* Moving stripes until there is a real percentage (from how many steps the last update took). */}
      <Progress
        value={phase === 'finished' ? 100 : (percent ?? 100)}
        striped={percent === null && phase !== 'finished'}
        animated={percent === null && phase !== 'finished'}
        aria-label="Update progress"
      />
      <Steps steps={steps} label="Update steps" />
      <Alert color="brand" variant="light">
        The site restarts while the update finishes, so this page may be briefly unavailable. It loads the new
        version by itself when the update is done.
      </Alert>
      <Output running={phase !== 'finished'} fallback={state.last_run.log_tail} />
    </Stack>
  );
}

/** A newer version that CI is still checking: since when, and about how long it takes. */
function Checking({ check }: { check: NonNullable<State['latest_check']> }) {
  const minutes = (count: number) => `${String(count)} ${plural(count, 'minute', 'minutes')}`;
  const since =
    check.minutes_running === null
      ? 'starting'
      : check.minutes_running < 1
        ? 'started just now'
        : `started ${minutes(check.minutes_running)} ago`;
  const usually = check.typical_minutes ? `usually about ${minutes(check.typical_minutes)}` : null;
  return (
    <Alert color="brand" variant="light">
      <Group gap="sm" wrap="nowrap" align="flex-start">
        <Loader size="xs" mt={3} aria-hidden />
        <Text size="sm">
          {`A new version is being checked (${[since, usually].filter(Boolean).join('; ')}). It is offered here once its checks have passed; this page looks again by itself.`}
        </Text>
      </Group>
    </Alert>
  );
}

function Install({
  state,
  busy,
  onStart,
}: {
  state: State;
  busy: boolean;
  onStart: (action: Action) => void;
}) {
  return (
    <Panel title="Install">
      <Stack gap="md">
        {!state.runner_installed ? (
          <Alert color="amber" variant="light">
            Updates cannot be started from here: the update runner is not installed on this server. Run the
            installer once from a shell to set it up.
          </Alert>
        ) : state.request_never_picked_up ? (
          <Alert color="amber" variant="light">
            An update was asked for but never started: the update runner on the server is not picking up
            requests. Run “update” from a shell, or run the installer once to set the runner up again.
          </Alert>
        ) : state.update_available ? (
          <Alert color="amber" variant="light">
            A newer version is available.
          </Alert>
        ) : state.latest_check?.state === 'running' ? (
          <Checking check={state.latest_check} />
        ) : state.latest_check?.state === 'failed' ? (
          <Alert color="red" variant="light">
            The newest version did not pass its checks, so it is not offered.{' '}
            {state.latest_check.url ? (
              <Anchor href={state.latest_check.url} target="_blank" rel="noreferrer">
                See what failed
              </Anchor>
            ) : null}
          </Alert>
        ) : !state.latest_check_failed ? (
          <Alert color="green" variant="light">
            This installation is up to date.
          </Alert>
        ) : null}
        <PresenceNote />
        <Group gap="md">
          <ConfirmButton
            color="brand"
            confirmLabel="Yes, install"
            disabled={
              !state.runner_installed || state.in_progress || (state.newer_version && !state.update_available)
            }
            loading={busy}
            onConfirm={() => {
              onStart('update');
            }}
          >
            Install update now
          </ConfirmButton>
          <Text size="sm" c="dimmed">
            Starts at once and takes a few minutes; this page follows along.
          </Text>
        </Group>
      </Stack>
    </Panel>
  );
}

function Rollback({
  state,
  busy,
  onStart,
}: {
  state: State;
  busy: boolean;
  onStart: (action: Action) => void;
}) {
  const point = state.rollback_point;
  return (
    <Panel title="Go back to the previous version">
      {point ? (
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            If an update caused a problem, this returns the site to the version that ran before it. It
            restores the program only: data added since stays, and the database structure is left as it is.
          </Text>
          <Details
            items={[
              ['Would return to', <Code key="to">{point.short_revision}</Code>],
              ...(point.recorded_at
                ? [['Recorded', formatDateTime(point.recorded_at)] satisfies Detail]
                : []),
              ...(point.database_backup
                ? [['Database backup', <Code key="backup">{point.database_backup}</Code>] satisfies Detail]
                : []),
            ]}
          />
          <Group>
            <ConfirmButton
              confirmLabel="Yes, go back"
              disabled={!state.runner_installed || state.in_progress}
              loading={busy}
              onConfirm={() => {
                onStart('rollback');
              }}
            >
              {`Roll back to ${point.short_revision}`}
            </ConfirmButton>
          </Group>
        </Stack>
      ) : (
        // Said, so that nothing here does not look the same as a point that cannot be read.
        <Text size="sm" c="dimmed">
          No rollback point has been recorded yet. One is written just before each update, so this appears
          after the next one.
        </Text>
      )}
    </Panel>
  );
}

function LastRun({ run, steps }: { run: State['last_run']; steps: Schemas['UpdateStepOut'][] }) {
  const result =
    run.state === 'completed' ? (
      <Pill tone="active">Completed</Pill>
    ) : run.state === 'failed' && run.interrupted ? (
      <Stack gap={4}>
        <Pill tone="failed">Did not finish</Pill>
        <Text size="sm" c="dimmed">
          {run.interrupted}
        </Text>
      </Stack>
    ) : run.state === 'failed' ? (
      <Group gap="xs">
        <Pill tone="failed">Failed</Pill>
        <Text size="sm" c="dimmed">
          exit code {run.exit_code ?? '?'}
        </Text>
      </Group>
    ) : (
      <Pill tone="info">{run.state ?? ''}</Pill>
    );
  return (
    <Panel title="Last update">
      <Stack gap="md">
        <Details
          items={[
            ['Result', result],
            ...(run.finished_at ? [['Finished', formatDateTime(run.finished_at)] satisfies Detail] : []),
            ...(run.revision_after
              ? [['Version after', <Code key="after">{run.revision_after}</Code>] satisfies Detail]
              : []),
          ]}
        />
        <Steps steps={steps} label="Steps of the last update" />
        <Output running={false} fallback={run.log_tail} />
      </Stack>
    </Panel>
  );
}

export function Updates() {
  const client = useQueryClient();
  // Whether an update ran while this page was open: then its end means loading the new version.
  const [watching, setWatching] = useState(false);
  const updates = useQuery({
    queryKey: KEY,
    queryFn: () => call(api.GET('/api/v1/admin/settings/updates')),
    refetchInterval: (query) =>
      watching || query.state.data?.in_progress
        ? POLL_MS
        : query.state.data?.latest_check?.state === 'running'
          ? CI_POLL_MS
          : false,
    refetchIntervalInBackground: true,
  });
  const state = updates.data;
  if (state?.in_progress && !watching) setWatching(true);
  const finished = watching && state !== undefined && !state.in_progress && !updates.isError;

  useEffect(() => {
    if (!finished) return;
    const timer = window.setTimeout(reloadPage, RELOAD_AFTER_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [finished]);

  const check = useMutation({
    mutationFn: () =>
      call(api.GET('/api/v1/admin/settings/updates', { params: { query: { refresh: true } } })),
    onSuccess: (fresh) => {
      client.setQueryData(KEY, fresh);
    },
    onError: notifyFailed,
  });
  const start = useMutation({
    mutationFn: (action: Action) => call(api.POST('/api/v1/admin/settings/updates', { body: { action } })),
    onSuccess: (fresh) => {
      setWatching(true);
      client.setQueryData(KEY, fresh);
    },
    onError: notifyFailed,
  });

  const phase = finished ? 'finished' : updates.isError ? 'restarting' : 'running';
  return (
    <>
      <PageHeader
        title="Updates"
        description="The version running, the newest one, and going back."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Settings' }, { label: 'Updates' }]}
      />
      {state === undefined ? (
        updates.isError ? (
          <ErrorState error={updates.error} onRetry={() => void updates.refetch()} />
        ) : (
          <LoadingState />
        )
      ) : (
        <Stack gap="lg">
          <Version
            state={state}
            checking={check.isPending}
            onCheck={() => {
              check.mutate();
            }}
          />
          {state.in_progress || watching ? (
            <Panel title="Update">
              <Running state={state} phase={phase} />
            </Panel>
          ) : (
            <>
              <Install
                state={state}
                busy={start.isPending && start.variables === 'update'}
                onStart={start.mutate}
              />
              <Rollback
                state={state}
                busy={start.isPending && start.variables === 'rollback'}
                onStart={start.mutate}
              />
              {state.last_run.state ? <LastRun run={state.last_run} steps={state.progress.steps} /> : null}
            </>
          )}
        </Stack>
      )}
    </>
  );
}
