/**
 * Settings -> Devices -> Firmware (docs/devices-plan.md, "Firmware updates"):
 * upload a firmware file -- its version, project, build and chip are read
 * from the file, and it is refused unless it is a whole app of the same
 * project as before -- choose which version every device runs, and see where
 * each device stands, refreshed while the page is open. One device can run
 * another version: chosen on its page. Data: /api/v1/admin/devices/firmware
 * (aeronautics_members/api/device_firmware.py).
 */
import { Button, FileInput, Group, Stack, Table, Text, TextInput } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router';

import { api, ApiError, call, type Schemas } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { formatDateTime } from '../../../lib/format';
import { EVERY_5_SECONDS, useLiveRefresh } from '../../../lib/live';
import { notifyDone, notifyFailed } from '../../../lib/notify';
import { devicesKey, FIRMWARE_STATE, firmwareKey, formatSize } from './deviceParts';

type FirmwareOut = Schemas['FirmwareOut'];
type Release = Schemas['FirmwareReleaseOut'];

function useFirmwareChange<T>(run: (input: T) => Promise<FirmwareOut>, done: (input: T) => string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async (data, input) => {
      client.setQueryData(firmwareKey, data);
      notifyDone(done(input));
      // The devices' pages; this page's own data is the answer already.
      await client.invalidateQueries({
        queryKey: devicesKey,
        predicate: (query) => query.queryKey[2] !== firmwareKey[2],
      });
    },
    onError: notifyFailed,
  });
}

function Upload({ data }: { data: FirmwareOut }) {
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [notes, setNotes] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const upload = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('Choose a file.');
      const form = new FormData();
      form.append('file', file);
      if (notes.trim()) form.append('notes', notes.trim());
      // openapi-fetch sends a FormData as it is, with its own Content-Type.
      return call(api.POST('/api/v1/admin/devices/firmware', { body: form as unknown as { file: string } }));
    },
    onSuccess: (result) => {
      setErrors({});
      setFile(null);
      setNotes('');
      client.setQueryData(firmwareKey, result);
      notifyDone(`Firmware ${result.releases[0]?.version ?? ''} uploaded.`);
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  return (
    <Panel title="Upload firmware">
      <Stack gap="md">
        <Group align="flex-start" gap="md" grow preventGrowOverflow={false}>
          <FileInput
            label="Firmware file"
            description={
              data.project
                ? `The app's .bin, of “${data.project}”; at most ${formatSize(data.max_size)}.`
                : `The app's .bin; at most ${formatSize(data.max_size)}.`
            }
            placeholder="Choose a .bin file"
            accept=".bin,application/octet-stream"
            clearable
            value={file}
            error={errors.file ?? null}
            onChange={setFile}
          />
          <TextInput
            label="Notes (optional)"
            description="What changed, for whoever chooses it."
            maxLength={300}
            value={notes}
            error={errors.notes ?? null}
            onChange={(event) => {
              setNotes(event.currentTarget.value);
            }}
          />
        </Group>
        <Group justify="flex-end">
          <Button
            loading={upload.isPending}
            disabled={!file}
            onClick={() => {
              upload.mutate();
            }}
          >
            Upload
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

function ReleaseRow({ release }: { release: Release }) {
  const choose = useFirmwareChange(
    (releaseId: number | null) =>
      call(api.PUT('/api/v1/admin/devices/firmware/chosen', { body: { release_id: releaseId } })),
    (releaseId) =>
      releaseId === null ? 'No update is offered now.' : `Every device installs ${release.version} now.`,
  );
  const remove = useFirmwareChange(
    () =>
      call(
        api.DELETE('/api/v1/admin/devices/firmware/{release_id}', {
          params: { path: { release_id: release.id } },
        }),
      ),
    () => `Firmware ${release.version} deleted.`,
  );
  const used = release.chosen || release.chosen_for > 0;
  return (
    <Table.Tr>
      <Table.Td>
        <Group gap="xs" wrap="nowrap">
          <Text fw={600}>{release.version}</Text>
          {release.chosen ? <Pill tone="active">Every device</Pill> : null}
        </Group>
        {release.notes ? (
          <Text size="xs" c="dimmed">
            {release.notes}
          </Text>
        ) : null}
      </Table.Td>
      <Table.Td>
        <Text size="sm">{release.built ?? '–'}</Text>
        <Text size="xs" c="dimmed">
          {[release.chip, release.idf_version, formatSize(release.size)].filter(Boolean).join(' · ')}
        </Text>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{formatDateTime(release.uploaded_at)}</Text>
        <Text size="xs" c="dimmed">
          {release.uploaded_by ?? ''}
        </Text>
      </Table.Td>
      <Table.Td>
        {release.running}
        {release.chosen_for ? (
          <Text size="xs" c="dimmed">
            {`chosen for ${String(release.chosen_for)}`}
          </Text>
        ) : null}
      </Table.Td>
      <Table.Td>
        <Group gap="xs" wrap="nowrap" justify="flex-end">
          {release.chosen ? (
            <ConfirmButton
              variant="default"
              size="xs"
              confirmLabel="Yes, stop"
              loading={choose.isPending}
              onConfirm={() => {
                choose.mutate(null);
              }}
            >
              Stop offering
            </ConfirmButton>
          ) : (
            <ConfirmButton
              size="xs"
              confirmLabel={`Yes, install ${release.version}`}
              loading={choose.isPending}
              onConfirm={() => {
                choose.mutate(release.id);
              }}
            >
              Install on every device
            </ConfirmButton>
          )}
          {used ? null : (
            <ConfirmButton
              variant="default"
              size="xs"
              confirmLabel="Yes, delete"
              loading={remove.isPending}
              onConfirm={() => {
                remove.mutate(undefined);
              }}
            >
              Delete
            </ConfirmButton>
          )}
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}

function Releases({ data }: { data: FirmwareOut }) {
  return (
    <Panel title="Firmware" flush>
      {data.releases.length ? (
        <Table.ScrollContainer minWidth={760} type="native">
          <Table verticalSpacing="sm" aria-label="Firmware">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Version</Table.Th>
                <Table.Th scope="col">Built</Table.Th>
                <Table.Th scope="col">Uploaded</Table.Th>
                <Table.Th scope="col">Running</Table.Th>
                <Table.Th scope="col" aria-label="Actions" />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.releases.map((release) => (
                <ReleaseRow key={release.id} release={release} />
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      ) : (
        <Stack px="md">
          <EmptyState>None uploaded yet.</EmptyState>
        </Stack>
      )}
    </Panel>
  );
}

function DeviceStates({ data }: { data: FirmwareOut }) {
  return (
    <Panel title="Devices" flush>
      {data.devices.length ? (
        <Table.ScrollContainer minWidth={560} type="native">
          <Table verticalSpacing="sm" aria-label="Devices' firmware">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Device</Table.Th>
                <Table.Th scope="col">Runs</Table.Th>
                <Table.Th scope="col">Should run</Table.Th>
                <Table.Th scope="col">State</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.devices.map((device) => {
                const state = FIRMWARE_STATE[device.state];
                return (
                  <Table.Tr key={device.id}>
                    <Table.Td>
                      <Text
                        component={Link}
                        to={`/admin/settings/devices/${String(device.id)}`}
                        fw={600}
                        size="sm"
                      >
                        {device.label}
                      </Text>
                    </Table.Td>
                    <Table.Td>{device.running ?? '–'}</Table.Td>
                    <Table.Td>
                      {device.wanted ?? '–'}
                      {device.own ? (
                        <Text size="xs" c="dimmed">
                          Chosen for this device
                        </Text>
                      ) : null}
                    </Table.Td>
                    <Table.Td>
                      <Pill tone={state.tone}>{state.label}</Pill>
                      {device.failed_detail ? (
                        <Text size="xs" c="dimmed">
                          {device.failed_detail}
                        </Text>
                      ) : null}
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      ) : (
        <Stack px="md">
          <EmptyState>No devices yet.</EmptyState>
        </Stack>
      )}
    </Panel>
  );
}

export function Firmware() {
  const loaded = useQuery({
    queryKey: firmwareKey,
    queryFn: () => call(api.GET('/api/v1/admin/devices/firmware')),
  });
  // Watching devices update: they report within seconds.
  useLiveRefresh(firmwareKey, EVERY_5_SECONDS);
  return (
    <>
      <PageHeader
        title="Firmware"
        description="One firmware for every device. Each installs the chosen version when it is not in use."
        crumbs={[
          { label: 'Admin', to: '/admin' },
          { label: 'Settings' },
          { label: 'Devices', to: '/admin/settings/devices' },
          { label: 'Firmware' },
        ]}
      />
      {loaded.isPending ? (
        <LoadingState />
      ) : loaded.isError ? (
        <ErrorState error={loaded.error} onRetry={() => void loaded.refetch()} />
      ) : (
        <Stack gap="lg">
          <Releases data={loaded.data} />
          <Upload data={loaded.data} />
          <DeviceStates data={loaded.data} />
        </Stack>
      )}
    </>
  );
}
