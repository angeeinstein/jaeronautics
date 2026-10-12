/**
 * Settings -> Devices: card readers and whatever else talks to the portal
 * (docs/devices-plan.md). Every device with its state, refreshed while the
 * page is open, and adding one -- whose key is then shown the one time it can
 * be. One device's page: Device.tsx. Data: /api/v1/admin/devices
 * (aeronautics_members/api/devices.py).
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
import { EVERY_30_SECONDS, useLiveRefresh } from '../../../lib/live';
import { notifyFailed } from '../../../lib/notify';
import {
  type Device,
  type DeviceKey,
  devicesKey,
  KeyShown,
  type Settings,
  statusOf,
  TypeSettingsFields,
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
          {device.name}
        </Text>
        {device.place ? (
          <Text size="xs" c="dimmed">
            {device.place}
          </Text>
        ) : null}
      </Table.Td>
      <Table.Td>{device.type_label}</Table.Td>
      <Table.Td>
        <Pill tone={status.tone}>{status.label}</Pill>
        <Text size="xs" c="dimmed">
          {status.detail}
        </Text>
      </Table.Td>
      <Table.Td>{device.firmware ?? '–'}</Table.Td>
    </Table.Tr>
  );
}

function AddDevice({
  types,
  onAdded,
}: {
  types: Schemas['DeviceTypeOut'][];
  onAdded: (key: DeviceKey) => void;
}) {
  const client = useQueryClient();
  const [name, setName] = useState('');
  const [typeSlug, setTypeSlug] = useState<string | null>(
    types.length === 1 ? (types[0]?.slug ?? null) : null,
  );
  const [place, setPlace] = useState('');
  const [settings, setSettings] = useState<Settings>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const type = types.find((item) => item.slug === typeSlug);
  const add = useMutation({
    mutationFn: () =>
      call(api.POST('/api/v1/admin/devices', { body: { name, type: typeSlug ?? '', place, settings } })),
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
        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
          <TextInput
            label="Name"
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
              type={type}
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
  useLiveRefresh(devicesKey, EVERY_30_SECONDS);
  return (
    <Stack gap="lg">
      <Panel title="Devices" flush>
        {data.items.length ? (
          <Table.ScrollContainer minWidth={560} type="native">
            <Table verticalSpacing="sm" aria-label="Devices">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Name</Table.Th>
                  <Table.Th scope="col">Type</Table.Th>
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
        <AddDevice types={data.types} onAdded={setShown} />
      )}
      <Text size="sm" c="dimmed">
        Devices talk to <code>{data.server}</code>, each with its own key.
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
