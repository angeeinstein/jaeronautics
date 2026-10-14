/**
 * Settings -> Devices: card readers and whatever else talks to the portal
 * (docs/devices-plan.md). Every device with its owner, state and firmware,
 * refreshed while the page is open; devices waiting to be paired, each a
 * click from its approval page (PairDevice.tsx); the firmware (Firmware.tsx);
 * and adding one by hand, for development: its type and its owner, the key
 * shown the one time it can be. One device's page: Device.tsx. Data:
 * /api/v1/admin/devices (aeronautics_members/api/devices.py).
 */
import { Button, Group, Select, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router';

import { api, ApiError, call, type Schemas } from '../../../api/client';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { formatDateTime } from '../../../lib/format';
import { EVERY_5_SECONDS, EVERY_30_SECONDS, useLiveRefresh } from '../../../lib/live';
import { notifyFailed } from '../../../lib/notify';
import {
  canBe,
  type Device,
  type DeviceKey,
  devicesKey,
  KeyShown,
  type Pairing,
  type Settings,
  statusOf,
  TypeSettingsFields,
  deviceFields,
  valuesOf,
} from './deviceParts';

const devicesQuery = {
  queryKey: devicesKey,
  queryFn: () => call(api.GET('/api/v1/admin/devices')),
};

function DeviceRow({ device }: { device: Device }) {
  const status = statusOf(device);
  return (
    <Table.Tr>
      <Table.Td>
        <Text component={Link} to={`/admin/settings/devices/${String(device.id)}`} fw={600} size="sm">
          {device.label}
        </Text>
        <Text size="xs" c="dimmed">
          {[device.name ? device.code : null, device.place].filter(Boolean).join(' · ') || null}
        </Text>
      </Table.Td>
      <Table.Td>{device.type_label}</Table.Td>
      <Table.Td>{device.owner_label}</Table.Td>
      <Table.Td>
        <Pill tone={status.tone}>{status.label}</Pill>
        <Text size="xs" c="dimmed">
          {status.detail}
        </Text>
      </Table.Td>
      <Table.Td>
        {device.firmware ?? '–'}
        {device.firmware_state === 'waiting' && device.firmware_wanted ? ` → ${device.firmware_wanted}` : ''}
        {device.firmware_state === 'failed' ? (
          <Text size="xs" c="red.4">
            {`${device.firmware_wanted ?? ''} failed`}
          </Text>
        ) : null}
      </Table.Td>
    </Table.Tr>
  );
}

function Waiting({ pairings, types }: { pairings: Pairing[]; types: Schemas['DeviceTypeOut'][] }) {
  return (
    <Panel title="Waiting to be paired" flush>
      <Table.ScrollContainer minWidth={560} type="native">
        <Table verticalSpacing="sm" aria-label="Waiting to be paired">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Code</Table.Th>
              <Table.Th scope="col">Board</Table.Th>
              <Table.Th scope="col">Asked</Table.Th>
              <Table.Th scope="col" aria-label="Pair" />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {pairings.map((pairing) => (
              <Table.Tr key={pairing.code}>
                <Table.Td>
                  <Text ff="monospace" fw={600}>
                    {pairing.code}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">
                    {[pairing.board, pairing.hardware_id].filter(Boolean).join(' · ') || '–'}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {[
                      pairing.firmware ? `Firmware ${pairing.firmware}` : null,
                      `Can be: ${canBe(types, pairing.supported_types)}`,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </Text>
                </Table.Td>
                <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateTime(pairing.created_at)}</Table.Td>
                <Table.Td>
                  <Button
                    component={Link}
                    to={`/admin/settings/devices/pair?code=${encodeURIComponent(pairing.code)}`}
                    size="xs"
                  >
                    Pair
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Panel>
  );
}

function AddDevice({
  types,
  owners,
  onAdded,
}: {
  types: Schemas['DeviceTypeOut'][];
  owners: Schemas['DeviceOwnerOut'][];
  onAdded: (key: DeviceKey) => void;
}) {
  const client = useQueryClient();
  const [name, setName] = useState('');
  const [owner, setOwner] = useState('association');
  const [typeSlug, setTypeSlug] = useState<string | null>(
    types.length === 1 ? (types[0]?.slug ?? null) : null,
  );
  const [place, setPlace] = useState('');
  const [settings, setSettings] = useState<Settings>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const type = types.find((item) => item.slug === typeSlug);
  const add = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/admin/devices', {
          body: {
            name: name || null,
            type: typeSlug ?? '',
            owner,
            place,
            settings: valuesOf(settings, deviceFields(type?.settings ?? [])),
          },
        }),
      ),
    onSuccess: async (result) => {
      setErrors({});
      setName('');
      setPlace('');
      setSettings({});
      onAdded(result);
      await client.invalidateQueries({ queryKey: devicesKey });
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });

  return (
    <Panel title="Add a device">
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="md">
          <Select
            label="Owner"
            description="Its owner names it and chooses what it does."
            data={owners}
            value={owner}
            error={errors.owner ?? null}
            allowDeselect={false}
            onChange={(next) => {
              setOwner(next ?? 'association');
              setSettings({});
            }}
          />
          <TextInput
            label="Name (optional)"
            description="Its code stands in for it."
            placeholder="Fridge reader"
            maxLength={80}
            value={name}
            error={errors.name ?? null}
            onChange={(event) => {
              setName(event.currentTarget.value);
            }}
          />
          <Select
            label="Type"
            placeholder="Choose"
            data={types.map((item) => ({ value: item.slug, label: item.label }))}
            value={typeSlug}
            error={errors.type ?? null}
            allowDeselect={false}
            onChange={(next) => {
              setTypeSlug(next);
              setSettings({});
            }}
          />
          <TextInput
            label="Place (optional)"
            placeholder="Workshop, beside the fridge"
            maxLength={120}
            value={place}
            error={errors.place ?? null}
            onChange={(event) => {
              setPlace(event.currentTarget.value);
            }}
          />
        </SimpleGrid>
        {type ? (
          <>
            <Text size="sm" c="dimmed">
              {type.description}
            </Text>
            <TypeSettingsFields
              settings={deviceFields(type.settings)}
              owner={owner}
              values={settings}
              errors={errors}
              onChange={(field, value) => {
                setSettings({ ...settings, [field]: value });
              }}
            />
          </>
        ) : null}
        <Group justify="flex-end">
          <Button
            loading={add.isPending}
            disabled={!typeSlug}
            onClick={() => {
              add.mutate();
            }}
          >
            Add
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

function DeviceList({ data }: { data: Schemas['DevicesOut'] }) {
  const [shown, setShown] = useState<DeviceKey | null>(null);
  // Faster while a device waits to be paired: it shows up as soon as it asks.
  useLiveRefresh(devicesKey, data.pairings.length ? EVERY_5_SECONDS : EVERY_30_SECONDS);
  return (
    <Stack gap="lg">
      {data.pairings.length ? <Waiting pairings={data.pairings} types={data.types} /> : null}
      <Panel title="Devices" flush>
        {data.items.length ? (
          <Table.ScrollContainer minWidth={680} type="native">
            <Table verticalSpacing="sm" aria-label="Devices">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Name</Table.Th>
                  <Table.Th scope="col">Type</Table.Th>
                  <Table.Th scope="col">Owner</Table.Th>
                  <Table.Th scope="col">State</Table.Th>
                  <Table.Th scope="col">Firmware</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.items.map((device) => (
                  <DeviceRow key={device.id} device={device} />
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
      {shown ? (
        <KeyShown
          result={shown}
          onDone={() => {
            setShown(null);
          }}
        />
      ) : (
        <AddDevice types={data.types} owners={data.owners} onAdded={setShown} />
      )}
      <Text size="sm" c="dimmed">
        Devices talk to <code>{data.server}</code>, each with its own key. A device paired by its code gets
        its key itself; adding one here shows the key to copy, for development.
      </Text>
    </Stack>
  );
}

export function Devices() {
  // Not SettingsPage: that starts its content afresh whenever the data
  // changes, which would take away a key just shown, or a form half filled
  // in, every time the list refreshes itself.
  const loaded = useQuery(devicesQuery);
  return (
    <>
      <PageHeader
        title="Devices"
        description="Card readers and other devices that talk to the portal."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Settings' }, { label: 'Devices' }]}
        actions={
          <Group gap="sm">
            <Button component={Link} to="/admin/settings/devices/firmware" variant="default">
              Firmware
            </Button>
            <Button component={Link} to="/admin/settings/devices/pair">
              Pair a device
            </Button>
          </Group>
        }
      />
      {loaded.isPending ? (
        <LoadingState />
      ) : loaded.isError ? (
        <ErrorState error={loaded.error} onRetry={() => void loaded.refetch()} />
      ) : (
        <DeviceList data={loaded.data} />
      )}
    </>
  );
}
