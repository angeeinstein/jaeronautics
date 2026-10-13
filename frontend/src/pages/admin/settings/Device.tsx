/**
 * Settings -> Devices -> one device (docs/devices-plan.md): its state; its
 * name, place and settings; switching it off; its key on one line (rotate,
 * revoke); every request a device of its type can send -- described, as a
 * command, and sent from here to see the answer; its recent requests and
 * events, refreshed every few seconds while the page is open; removing it.
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
import { EVERY_5_SECONDS, useLiveRefresh } from '../../../lib/live';
import { notifyDone, notifyFailed, notifyNote } from '../../../lib/notify';
import {
  type Device,
  type DeviceKey,
  devicesKey,
  type DeviceType,
  KeyReveal,
  type Settings,
  statusOf,
  TypeSettingsFields,
} from './deviceParts';

type Detail = Schemas['DeviceDetailOut'];

const EVENT_LABELS: Record<string, string> = {
  added: 'Added',
  switched: 'Switched',
  key: 'Key',
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

/** One request a device can send: what it is, the command to send it, and -- sent from here -- its answer. */
interface DeviceRequest {
  id: string;
  method: 'GET' | 'POST';
  path: string;
  title: string;
  description: string;
  curl: string;
  /** What the page sends to try it; none: not sent from here. */
  tryBody: ((data: Record<string, unknown>) => Schemas['DeviceTryIn']) | null;
  /** A report: it carries data. */
  report: boolean;
}

function requestsOf(server: string, type: DeviceType | undefined): DeviceRequest[] {
  const bearer = `-H "Authorization: Bearer <key>"`;
  return [
    {
      id: 'hello',
      method: 'GET',
      path: '/device',
      title: 'Sign of life',
      description: 'Who the device is, its settings and the time. Sent at start and every few minutes.',
      curl: `curl ${bearer} -H "X-Device-Firmware: 1.0.0" ${server}`,
      tryBody: () => ({ what: 'hello' }),
      report: false,
    },
    ...(type?.resources ?? []).map((resource): DeviceRequest => ({
      id: `resource-${resource.name}`,
      method: 'GET',
      path: `/device/resources/${resource.name}`,
      title: `Fetch “${resource.name}”`,
      description: resource.description,
      curl: `curl ${bearer} ${server}/resources/${resource.name}`,
      tryBody: () => ({ what: 'resource', name: resource.name }),
      report: false,
    })),
    ...(type?.reports ?? []).map((report): DeviceRequest => ({
      id: `report-${report.name}`,
      method: 'POST',
      path: '/device/events',
      title: `Report “${report.name}”`,
      description: report.description,
      curl: [
        `curl ${bearer} -H "Content-Type: application/json" \\`,
        `     -d '{"event_id": "boot-1", "kind": "${report.name}", "data": {}}' ${server}/events`,
      ].join('\n'),
      tryBody: report.tryable ? (data) => ({ what: 'report', name: report.name, data }) : null,
      report: true,
    })),
  ];
}

/** Why the device itself would be refused now, if it would. */
function refusedNow(device: Device) {
  if (!device.enabled) return 'it is switched off (403 device_off)';
  if (!device.key_hint) return 'it has no key (401 device_key_revoked)';
  return null;
}

function DeviceApi({
  device,
  server,
  type,
}: {
  device: Device;
  server: string;
  type: DeviceType | undefined;
}) {
  const client = useQueryClient();
  const [data, setData] = useState<Record<string, string>>({});
  const [answer, setAnswer] = useState<{ id: string; text: string } | null>(null);
  const send = useMutation({
    mutationFn: ({ body }: { id: string; body: Schemas['DeviceTryIn'] }) =>
      call(
        api.POST('/api/v1/admin/devices/{device_id}/try', {
          params: { path: { device_id: device.id } },
          body,
        }),
      ),
    onSuccess: async (result, { id }) => {
      setAnswer({ id, text: JSON.stringify(result.answer, null, 2) });
      await client.invalidateQueries({ queryKey: [...devicesKey, device.id] });
    },
    onError: notifyFailed,
  });
  const refused = refusedNow(device);

  const sendRequest = (request: DeviceRequest) => {
    if (!request.tryBody) return;
    let parsed: Record<string, unknown> = {};
    if (request.report) {
      try {
        const text = (data[request.id] ?? '').trim();
        parsed = JSON.parse(text === '' ? '{}' : text) as Record<string, unknown>;
      } catch {
        notifyNote('The data is not JSON.');
        return;
      }
    }
    send.mutate({ id: request.id, body: request.tryBody(parsed) });
  };

  return (
    <Panel title="What the device can send" flush>
      <Stack gap={0}>
        <Text size="sm" c="dimmed" p="md">
          Every request a device of this type can send, with the command to send it from a computer.{' '}
          <b>Send</b> sends it from here as the device and shows the answer; the device is not counted as
          seen.
          {refused ? ` The device itself would be refused now: ${refused}.` : ''}
        </Text>
        {requestsOf(server, type).map((request) => (
          <Stack
            key={request.id}
            gap="sm"
            p="md"
            style={{ borderTop: '1px solid var(--ja-border)' }}
            aria-label={request.title}
            role="group"
          >
            <Group justify="space-between" align="flex-start" gap="md">
              <Stack gap={4} miw={0} style={{ flex: 1 }}>
                <Group gap="sm">
                  <Text fw={600}>{request.title}</Text>
                  <Code>
                    {request.method} {request.path}
                  </Code>
                </Group>
                {request.description ? (
                  <Text size="sm" c="dimmed">
                    {request.description}
                  </Text>
                ) : null}
              </Stack>
              {request.tryBody ? (
                <Button
                  variant="default"
                  aria-label={`Send ${request.title}`}
                  loading={send.isPending && send.variables.id === request.id}
                  onClick={() => {
                    sendRequest(request);
                  }}
                >
                  Send
                </Button>
              ) : (
                <Text size="sm" c="dimmed">
                  Not sent from here: it would really happen.
                </Text>
              )}
            </Group>
            {request.report && request.tryBody ? (
              <Textarea
                label="Data"
                autosize
                minRows={1}
                styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)' } }}
                value={data[request.id] ?? '{}'}
                onChange={(event) => {
                  setData({ ...data, [request.id]: event.currentTarget.value });
                }}
              />
            ) : null}
            <Code block>{request.curl}</Code>
            {answer?.id === request.id ? (
              <Stack gap={4}>
                <Text size="sm" fw={600}>
                  Answer
                </Text>
                <Code block aria-label="Answer">
                  {answer.text}
                </Code>
              </Stack>
            ) : null}
          </Stack>
        ))}
        <Text size="sm" c="dimmed" p="md" style={{ borderTop: '1px solid var(--ja-border)' }}>
          HTTPS only, with the key as a bearer token: no session, no cookies. Refused: 401 for an unknown or
          revoked key, 403 when switched off, 404 for what its type does not offer, 429 when too often.
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

/** Its key on one line -- shortened, when made, when last used -- with what can be done to it. */
function KeyCard({ device }: { device: Device }) {
  const client = useQueryClient();
  const [shown, setShown] = useState<DeviceKey | null>(null);
  const path = { params: { path: { device_id: device.id } } };
  const done = async () => {
    await client.invalidateQueries({ queryKey: devicesKey });
  };
  const newKey = useMutation({
    mutationFn: () => call(api.POST('/api/v1/admin/devices/{device_id}/key', path)),
    onSuccess: async (result) => {
      setShown(result);
      await done();
    },
    onError: notifyFailed,
  });
  const revoke = useMutation({
    mutationFn: () => call(api.DELETE('/api/v1/admin/devices/{device_id}/key', path)),
    onSuccess: async () => {
      notifyDone('Key revoked: the device cannot sign in until it has a new one.');
      await done();
    },
    onError: notifyFailed,
  });

  return (
    <Panel title="Key">
      {shown ? (
        <KeyReveal
          result={shown}
          onDone={() => {
            setShown(null);
          }}
        />
      ) : device.key_hint ? (
        <Group justify="space-between" gap="md" align="center">
          <Stack gap={4} miw={0}>
            <Code style={{ wordBreak: 'break-all' }} aria-label="Key">
              {device.key_hint}
            </Code>
            <Text size="sm" c="dimmed">
              Made {formatDateTime(device.key_created_at)} ·{' '}
              {device.key_last_used_at
                ? `last used ${formatDateTime(device.key_last_used_at)}`
                : 'not used yet'}
            </Text>
          </Stack>
          <Group gap="sm" wrap="nowrap">
            <ConfirmButton
              variant="default"
              confirmLabel="Yes, rotate"
              loading={newKey.isPending}
              onConfirm={() => {
                newKey.mutate();
              }}
            >
              Rotate
            </ConfirmButton>
            <ConfirmButton
              variant="default"
              confirmLabel="Yes, revoke"
              loading={revoke.isPending}
              onConfirm={() => {
                revoke.mutate();
              }}
            >
              Revoke
            </ConfirmButton>
          </Group>
        </Group>
      ) : (
        <Group justify="space-between" gap="md" align="center">
          <Text size="sm" c="dimmed">
            No key: revoked {device.key_revoked_at ? formatDateTime(device.key_revoked_at) : ''}. The device
            cannot sign in.
          </Text>
          <Button
            loading={newKey.isPending}
            onClick={() => {
              newKey.mutate();
            }}
          >
            Make a key
          </Button>
        </Group>
      )}
      {!shown && device.key_hint ? (
        <Text size="xs" c="dimmed" mt="sm">
          Rotate: a new key works at once, and this one stops. Revoke: no key works until a new one is made.
        </Text>
      ) : null}
    </Panel>
  );
}

function Requests({ requests }: { requests: Detail['requests'] }) {
  return (
    <Panel title="Recent requests" flush>
      {requests.length ? (
        <Table.ScrollContainer minWidth={560} type="native">
          <Table verticalSpacing="xs" aria-label="Recent requests">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">When</Table.Th>
                <Table.Th scope="col">Request</Table.Th>
                <Table.Th scope="col">Answer</Table.Th>
                <Table.Th scope="col">From</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {requests.map((request) => (
                <Table.Tr key={request.id}>
                  <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateTime(request.at)}</Table.Td>
                  <Table.Td>
                    <Code>
                      {request.method} {request.path.replace(/^\/api\/v1/, '')}
                    </Code>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" c={request.status < 400 ? undefined : 'red.4'}>
                      {request.status}
                      {request.code ? ` ${request.code}` : ''}
                    </Text>
                  </Table.Td>
                  <Table.Td>{request.address ?? '–'}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      ) : (
        <Stack px="md">
          <EmptyState>None yet.</EmptyState>
        </Stack>
      )}
    </Panel>
  );
}

function RemoveDevice({ device }: { device: Device }) {
  const client = useQueryClient();
  const navigate = useNavigate();
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
    <Panel title="Remove the device">
      <Group justify="space-between" gap="md" align="center">
        <Text size="sm" c="dimmed">
          Deletes the device, its key, its requests and its events.
        </Text>
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
    </Panel>
  );
}

function DevicePage({ detail, types }: { detail: Detail; types: DeviceType[] }) {
  const { device } = detail;
  const type = types.find((item) => item.slug === device.type);
  return (
    <Stack gap="lg">
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
        <State device={device} />
        {/* Keyed by what was saved, so it starts afresh after a save, not after every refresh. */}
        <SettingsForm
          key={JSON.stringify([device.name, device.place, device.settings])}
          device={device}
          type={type}
        />
      </SimpleGrid>
      <KeyCard device={device} />
      <DeviceApi device={device} server={detail.server} type={type} />
      <Requests requests={detail.requests} />
      <Events events={detail.events} />
      <RemoveDevice device={device} />
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
  // Every few seconds: watching a device while its firmware is written.
  useLiveRefresh(key, EVERY_5_SECONDS);
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
