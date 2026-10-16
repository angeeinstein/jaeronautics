/**
 * Settings › Devices › one device, *Crashes* (#crashes; docs/devices-plan.md,
 * "Crash reports"): what the device reported after restarting from a crash,
 * the newest first -- when, the version that crashed (found by its build),
 * the task, why, and the call path -- with its core dump to download, for
 * `espcoredump.py info_corefile -c <dump> <program file>`. Admins only, and
 * only here. Refreshed every few seconds while the page is open.
 * Data: /api/v1/admin/devices/<id>/crashes (aeronautics_members/api/devices.py).
 */
import { Button, Code, Group, Stack, Text } from '@mantine/core';
import { IconBug } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api, call, type Schemas } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { formatDateTime } from '../../../lib/format';
import { EVERY_5_SECONDS, useLiveRefresh } from '../../../lib/live';
import { notifyDone, notifyFailed } from '../../../lib/notify';
import { devicesKey, formatSize } from './deviceParts';

type Crash = Schemas['DeviceCrashOut'];

export const REASON: Record<Crash['reset_reason'], string> = {
  panic: 'Crashed',
  task_watchdog: 'Task hung',
  interrupt_watchdog: 'Interrupts hung',
};

function CrashCard({ deviceId, crash }: { deviceId: number; crash: Crash }) {
  const client = useQueryClient();
  const remove = useMutation({
    mutationFn: () =>
      call(
        api.DELETE('/api/v1/admin/devices/{device_id}/crashes/{crash_id}', {
          params: { path: { device_id: deviceId, crash_id: crash.id } },
        }),
      ),
    onSuccess: (data) => {
      client.setQueryData([...devicesKey, deviceId, 'crashes'], data);
      notifyDone('Crash report deleted.');
    },
    onError: notifyFailed,
  });
  const ran = crash.version ?? `Build ${crash.build}`;
  return (
    <Stack gap={6} py="sm" style={{ borderTop: '1px solid var(--ja-border)' }}>
      <Group justify="space-between" align="flex-start" gap="sm">
        <Stack gap={2}>
          <Group gap="xs">
            <Text fw={600}>{formatDateTime(crash.received_at)}</Text>
            <Pill tone="failed">{REASON[crash.reset_reason]}</Pill>
          </Group>
          <Text size="sm" c="dimmed">
            {[
              ran,
              `task ${crash.task}`,
              crash.reported_by && crash.reported_by !== crash.version
                ? `reported by ${crash.reported_by}`
                : null,
            ]
              .filter(Boolean)
              .join(' · ')}
          </Text>
        </Stack>
        <Group gap="xs">
          {crash.has_file ? (
            // A download from Flask; not a page of the app.
            <Button
              component="a"
              href={`/api/v1/admin/devices/${String(deviceId)}/crashes/${String(crash.id)}/file`}
              download
              size="xs"
              variant="default"
            >
              {`Dump (${formatSize(crash.size)})`}
            </Button>
          ) : null}
          <ConfirmButton
            variant="default"
            size="xs"
            confirmLabel="Yes, delete"
            loading={remove.isPending}
            onConfirm={() => {
              remove.mutate();
            }}
          >
            Delete
          </ConfirmButton>
        </Group>
      </Group>
      <Code block aria-label="Call path">
        {[crash.pc, ...crash.backtrace].join(' ')}
      </Code>
      {crash.backtrace_corrupted ? (
        <Text size="xs" c="dimmed">
          The call path could not be read completely.
        </Text>
      ) : null}
      {crash.has_file ? null : (
        <Text size="xs" c="dimmed">
          No dump: not sent yet, or one of the device’s 20 oldest.
        </Text>
      )}
    </Stack>
  );
}

export function DeviceCrashes({ deviceId }: { deviceId: number }) {
  const key = [...devicesKey, deviceId, 'crashes'] as const;
  const loaded = useQuery({
    queryKey: key,
    queryFn: () =>
      call(
        api.GET('/api/v1/admin/devices/{device_id}/crashes', { params: { path: { device_id: deviceId } } }),
      ),
  });
  useLiveRefresh(key, EVERY_5_SECONDS);
  if (loaded.isPending) return <LoadingState />;
  if (loaded.isError) return <ErrorState error={loaded.error} onRetry={() => void loaded.refetch()} />;
  const { crashes } = loaded.data;
  return (
    <Panel icon={IconBug} title="Crashes">
      {crashes.length ? (
        <Stack gap={0}>
          <Text size="sm" c="dimmed" pb="sm">
            Read with the firmware’s program file: espcoredump.py info_corefile -c &lt;dump&gt; &lt;program
            file&gt;.
          </Text>
          {crashes.map((crash) => (
            <CrashCard key={crash.id} deviceId={deviceId} crash={crash} />
          ))}
        </Stack>
      ) : (
        <EmptyState>No crashes reported.</EmptyState>
      )}
    </Panel>
  );
}
