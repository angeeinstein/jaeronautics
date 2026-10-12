/**
 * Settings -> Devices -> one device (docs/devices-plan.md): its state,
 * refreshed while the page is open; its name, place and settings; switching
 * it off; a new key; trying what it would be answered; how a device talks to
 * the portal, for writing its firmware; its recent events; removing it.
 * Data: /api/v1/admin/devices/<id> (aeronautics_members/api/devices.py).
 */
import {
  Button,
  Code,
  Group,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router';

import { api, ApiError, call, type Schemas } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Details } from '../../../components/Details';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { formatDateTime } from '../../../lib/format';
import { EVERY_30_SECONDS, useLiveRefresh } from '../../../lib/live';
import { notifyDone, notifyFailed, notifyNote } from '../../../lib/notify';
import {
  type Device,
  type DeviceKey,
  devicesKey,
  type DeviceType,
  KeyShown,
  type Settings,
  statusOf,
  TypeSettingsFields,
} from './deviceParts';

type Detail = Schemas['DeviceDetailOut'];

const EVENT_LABELS: Record<string, string> = {
  added: 'Added',
  switched: 'Switched',
  key: 'New key',
  first: 'First contact',
  firmware: 'Firmware',
  refused: 'Refused',
  reported: 'Reported',
  tried: 'Tried',
};

function State({ device }: { device: Device }) {
  const status = statusOf(device);
  return (
    <Panel title="State">
      <Details
        items={[
          [
            'State',
            <Group key="state" gap="xs">
              <Pill tone={status.tone}>{status.label}</Pill>
              <Text size="sm" c="dimmed">
                {status.detail}
              </Text>
            </Group>,
          ],
          ['Type', device.type_label],
          ['Last seen', device.last_seen_at ? formatDateTime(device.last_seen_at) : 'Never'],
          ['From', device.last_address ?? '–'],
          ['Firmware', device.firmware ?? '–'],
          ['Key', `${device.key_hint}…, since ${formatDateTime(device.key_created_at)}`],
          ['Added', formatDateTime(device.created_at)],
        ]}
      />
    </Panel>
  );
}

function SettingsForm({ device, type }: { device: Device; type: DeviceType | undefined }) {
  const client = useQueryClient();
  const [name, setName] = useState(device.name);
  const [place, setPlace] = useState(device.place ?? '');
  const [settings, setSettings] = useState<Settings>(device.settings);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: (body: Schemas['DeviceChangeIn']) =>
      call(
        api.PATCH('/api/v1/admin/devices/{device_id}', { params: { path: { device_id: device.id } }, body }),
      ),
    onSuccess: async (_saved, body) => {
      setErrors({});
      if (body.enabled !== undefined && body.enabled !== null)
        notifyDone(body.enabled ? 'Switched on.' : 'Switched off: its key is refused from now on.');
      else notifyDone('Saved.');
      await client.invalidateQueries({ queryKey: devicesKey });
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });

  return (
    <Panel title="Settings">
      <Stack gap="md">
        <Switch
          label="On"
          description="Off: the device is refused, whatever key it has."
          checked={device.enabled}
          onChange={(event) => {
            save.mutate({ enabled: event.currentTarget.checked });
          }}
        />
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          <TextInput
            label="Name"
            maxLength={80}
            value={name}
            error={errors.name ?? null}
            onChange={(event) => {
              setName(event.currentTarget.value);
            }}
          />
          <TextInput
            label="Place (optional)"
            maxLength={120}
            value={place}
            error={errors.place ?? null}
            onChange={(event) => {
              setPlace(event.currentTarget.value);
            }}
          />
        </SimpleGrid>
        {type ? (
          <TypeSettingsFields
            type={type}
            values={settings}
            errors={errors}
            onChange={(field, value) => {
              setSettings({ ...settings, [field]: value });
            }}
          />
        ) : null}
        <Group justify="flex-end">
          <Button
            loading={save.isPending}
            onClick={() => {
              save.mutate({ name, place, settings });
            }}
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

function TryIt({ device, type }: { device: Device; type: DeviceType | undefined }) {
  const client = useQueryClient();
  const [data, setData] = useState('{}');
  const [answer, setAnswer] = useState<string | null>(null);
  const tryIt = useMutation({
    mutationFn: (body: Schemas['DeviceTryIn']) =>
      call(
        api.POST('/api/v1/admin/devices/{device_id}/try', {
          params: { path: { device_id: device.id } },
          body,
        }),
      ),
    onSuccess: async (result) => {
      setAnswer(JSON.stringify(result.answer, null, 2));
      await client.invalidateQueries({ queryKey: [...devicesKey, device.id] });
    },
    onError: notifyFailed,
  });
  const report = (name: string) => {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(data || '{}') as Record<string, unknown>;
    } catch {
      notifyNote('The data is not JSON.');
      return;
    }
    tryIt.mutate({ what: 'report', name, data: parsed });
  };

  return (
    <Panel title="Try it">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          What the device would be answered. It is not counted as the device being seen.
        </Text>
        <Group gap="sm">
          <Button
            variant="default"
            loading={tryIt.isPending}
            onClick={() => {
              tryIt.mutate({ what: 'hello' });
            }}
          >
            Sign of life
          </Button>
          {type?.resources.map((name) => (
            <Button
              key={name}
              variant="default"
              onClick={() => {
                tryIt.mutate({ what: 'resource', name });
              }}
            >
              Fetch “{name}”
            </Button>
          ))}
        </Group>
        {type?.tryable.length ? (
          <Stack gap="sm">
            <Textarea
              label="Data to report"
              autosize
              minRows={2}
              styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)' } }}
              value={data}
              onChange={(event) => {
                setData(event.currentTarget.value);
              }}
            />
            <Group gap="sm">
              {type.tryable.map((name) => (
                <Button
                  key={name}
                  variant="default"
                  onClick={() => {
                    report(name);
                  }}
                >
                  Report “{name}”
                </Button>
              ))}
            </Group>
          </Stack>
        ) : null}
        {answer ? (
          <Code block aria-label="Answer">
            {answer}
          </Code>
        ) : null}
      </Stack>
    </Panel>
  );
}

function HowItTalks({ server, type }: { server: string; type: DeviceType | undefined }) {
  const report = type?.reports[0] ?? 'ping';
  const resource = type?.resources[0] ?? 'hello';
  const lines = [
    `# Sign of life: who it is, its settings, the time`,
    `curl -H "Authorization: Bearer <key>" -H "X-Device-Firmware: 1.0.0" ${server}`,
    '',
    `# What its type offers it`,
    `curl -H "Authorization: Bearer <key>" ${server}/resources/${resource}`,
    '',
    `# Report something; the same event_id again gets the first answer`,
    `curl -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \\`,
    `     -d '{"event_id": "boot-1", "kind": "${report}", "data": {}}' ${server}/events`,
  ];
  return (
    <Panel title="How a device talks to the portal">
      <Stack gap="sm">
        <Code block>{lines.join('\n')}</Code>
        <Text size="sm" c="dimmed">
          HTTPS only, with the key as a bearer token: no session, no cookies. Refused: 401 for an unknown key,
          403 when switched off, 404 for what its type does not offer, 429 when too often.
        </Text>
      </Stack>
    </Panel>
  );
}

function Events({ events }: { events: Detail['events'] }) {
  return (
    <Panel title="Recent events" flush>
      {events.length ? (
        <Table.ScrollContainer minWidth={560} type="native">
          <Table verticalSpacing="xs" aria-label="Recent events">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">When</Table.Th>
                <Table.Th scope="col">What</Table.Th>
                <Table.Th scope="col">Detail</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {events.map((event) => (
                <Table.Tr key={event.id}>
                  <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateTime(event.at)}</Table.Td>
                  <Table.Td>
                    {EVENT_LABELS[event.kind] ?? event.kind}
                    {event.name ? ` “${event.name}”` : ''}
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{event.detail ?? (event.event_id ? `Event ${event.event_id}` : '')}</Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      ) : (
        <Stack px="md">
          <EmptyState>Nothing yet.</EmptyState>
        </Stack>
      )}
    </Panel>
  );
}

function KeyAndRemove({ device, onKey }: { device: Device; onKey: (key: DeviceKey) => void }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const newKey = useMutation({
    mutationFn: () =>
      call(api.POST('/api/v1/admin/devices/{device_id}/key', { params: { path: { device_id: device.id } } })),
    onSuccess: async (result) => {
      onKey(result);
      await client.invalidateQueries({ queryKey: devicesKey });
    },
    onError: notifyFailed,
  });
  const remove = useMutation({
    mutationFn: () =>
      call(api.DELETE('/api/v1/admin/devices/{device_id}', { params: { path: { device_id: device.id } } })),
    onSuccess: async () => {
      notifyDone(`${device.name} removed.`);
      await navigate('/admin/settings/devices');
      // The list only: this device's own data, asked for again, would be "not found".
      await client.invalidateQueries({ queryKey: devicesKey, exact: true });
    },
    onError: notifyFailed,
  });
  return (
    <Panel title="Key and removal">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          A new key works at once, and the device&apos;s current one stops working. Removing the device
          deletes it, its key and its events.
        </Text>
        <Group gap="sm">
          <ConfirmButton
            variant="default"
            confirmLabel="Yes, a new key"
            loading={newKey.isPending}
            onConfirm={() => {
              newKey.mutate();
            }}
          >
            New key
          </ConfirmButton>
          <ConfirmButton
            confirmLabel="Yes, remove it"
            loading={remove.isPending}
            onConfirm={() => {
              remove.mutate();
            }}
          >
            Remove
          </ConfirmButton>
        </Group>
      </Stack>
    </Panel>
  );
}

function DevicePage({ detail, types }: { detail: Detail; types: DeviceType[] }) {
  const [shown, setShown] = useState<DeviceKey | null>(null);
  const { device } = detail;
  const type = types.find((item) => item.slug === device.type);
  return (
    <Stack gap="lg">
      {shown ? (
        <KeyShown
          result={shown}
          onDone={() => {
            setShown(null);
          }}
        />
      ) : null}
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
        <State device={device} />
        {/* Keyed by what was saved, so it starts afresh after a save, not after every refresh. */}
        <SettingsForm
          key={JSON.stringify([device.name, device.place, device.settings])}
          device={device}
          type={type}
        />
      </SimpleGrid>
      <TryIt device={device} type={type} />
      <HowItTalks server={detail.server} type={type} />
      <Events events={detail.events} />
      <KeyAndRemove device={device} onKey={setShown} />
    </Stack>
  );
}

export function DeviceDetail() {
  const { deviceId = '' } = useParams();
  const id = Number(deviceId);
  const key = [...devicesKey, id] as const;
  const detail = useQuery({
    queryKey: key,
    queryFn: () =>
      call(api.GET('/api/v1/admin/devices/{device_id}', { params: { path: { device_id: id } } })),
  });
  // The types: the same list the devices page has.
  const list = useQuery({
    queryKey: devicesKey,
    queryFn: () => call(api.GET('/api/v1/admin/devices')),
  });
  useLiveRefresh(key, EVERY_30_SECONDS);
  const title = detail.data?.device.name ?? 'Device';
  return (
    <>
      <PageHeader
        title={title}
        description={detail.data?.device.place ?? undefined}
        crumbs={[
          { label: 'Admin', to: '/admin' },
          { label: 'Settings' },
          { label: 'Devices', to: '/admin/settings/devices' },
          { label: title },
        ]}
      />
      {detail.isPending || list.isPending ? (
        <LoadingState />
      ) : detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : (
        <DevicePage detail={detail.data} types={list.data.types} />
      )}
    </>
  );
}
