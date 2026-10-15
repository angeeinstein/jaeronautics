/**
 * An owner's devices (docs/devices-plan.md, "Owners"): a team's on its
 * management page. Each device in a card of its own -- online or not, its
 * code -- with what its owner may change: its name, place and description,
 * on or off; and what it reported lately, in words, so they "can actually
 * see that this is working". What is technical stays with admins; what a
 * sales point sells is chosen on the owner's Sales page (SalesPointMenus).
 */
import { Button, Group, SimpleGrid, Stack, Switch, Table, Text, TextInput } from '@mantine/core';
import { type QueryKey, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { IconCpu } from '@tabler/icons-react';

import { ApiError, type Schemas } from '../../api/client';
import {
  deviceFields,
  type Settings,
  TypeSettingsFields,
  valuesOf,
} from '../../pages/admin/settings/deviceParts';
import { formatDateTime } from '../../lib/format';
import { notifyDone, notifyFailed } from '../../lib/notify';
import { Panel } from '../Panel';
import { Pill } from '../Pill';
import { EmptyState } from '../States';

export type OwnedDevices = Schemas['OwnedDevicesOut'];
export type OwnedDevice = Schemas['OwnedDeviceOut'];
export type OwnedDeviceChange = Schemas['OwnedDeviceChangeIn'];

function state(device: OwnedDevice): {
  label: string;
  tone: 'active' | 'pending' | 'neutral';
  detail: string;
} {
  if (!device.enabled)
    return { label: 'Off', tone: 'neutral', detail: 'Switched off: it refuses every tap.' };
  if (device.online) return { label: 'Online', tone: 'active', detail: 'Spoke in the last five minutes.' };
  if (device.last_seen_at)
    return { label: 'Quiet', tone: 'pending', detail: `Last seen ${formatDateTime(device.last_seen_at)}.` };
  return { label: 'New', tone: 'neutral', detail: 'Has not spoken yet.' };
}

function DeviceCard({
  device,
  owner,
  queryKey,
  change,
}: {
  device: OwnedDevice;
  owner: string;
  queryKey: QueryKey;
  change: (id: number, body: OwnedDeviceChange) => Promise<OwnedDevices>;
}) {
  const client = useQueryClient();
  const [name, setName] = useState(device.name ?? '');
  const [place, setPlace] = useState(device.place ?? '');
  const [description, setDescription] = useState(device.description ?? '');
  const [settings, setSettings] = useState<Settings>(device.settings);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: (body: OwnedDeviceChange) => change(device.id, body),
    onSuccess: (data, body) => {
      setErrors({});
      client.setQueryData(queryKey, data);
      if (body.enabled === true) notifyDone(`${device.label} is on.`);
      else if (body.enabled === false) notifyDone(`${device.label} is off: it refuses every tap.`);
      else notifyDone('Saved.');
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const shown = state(device);
  const fields = deviceFields(device.fields);

  return (
    <Panel
      icon={IconCpu}
      title={device.label}
      actions={
        <Switch
          label="On"
          checked={device.enabled}
          disabled={save.isPending}
          onChange={(event) => {
            save.mutate({ enabled: event.currentTarget.checked });
          }}
        />
      }
    >
      <Stack gap="md">
        <Group gap="sm" wrap="wrap">
          <Pill tone={shown.tone}>{shown.label}</Pill>
          <Text size="sm" c="dimmed">
            {`${shown.detail} ${device.type_label}${device.name ? ` · ${device.code}` : ''}`}
          </Text>
        </Group>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            // Only the settings its owner may change here: the rest are an admin's, and a
            // sales point's menu is chosen on the Sales page.
            const own = valuesOf(settings, fields);
            save.mutate({ name, place, description, ...(fields.length ? { settings: own } : {}) });
          }}
        >
          <Stack gap="sm">
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              <TextInput
                label="Name (optional)"
                placeholder={device.code}
                maxLength={30}
                value={name}
                error={errors.name ?? null}
                onChange={(event) => {
                  setName(event.currentTarget.value);
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
            <TextInput
              label="Description (optional)"
              maxLength={300}
              value={description}
              error={errors.description ?? null}
              onChange={(event) => {
                setDescription(event.currentTarget.value);
              }}
            />
            <TypeSettingsFields
              settings={fields}
              owner={owner}
              values={settings}
              errors={errors}
              onChange={(field, value) => {
                setSettings({ ...settings, [field]: value });
              }}
            />
            <Group justify="flex-end">
              <Button type="submit" loading={save.isPending}>
                Save
              </Button>
            </Group>
          </Stack>
        </form>
        <Stack gap={4}>
          <Text size="sm" fw={600}>
            Lately
          </Text>
          {device.recent.length ? (
            <Table.ScrollContainer minWidth={420} type="native">
              <Table verticalSpacing={4} aria-label={`What ${device.label} reported lately`}>
                <Table.Tbody>
                  {device.recent.map((report, index) => (
                    <Table.Tr key={`${report.at}-${String(index)}`}>
                      <Table.Td style={{ whiteSpace: 'nowrap', width: '1%' }}>
                        <Text size="sm" c="dimmed">
                          {formatDateTime(report.at)}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm">{report.detail}</Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          ) : (
            <Text size="sm" c="dimmed">
              Nothing yet.
            </Text>
          )}
        </Stack>
      </Stack>
    </Panel>
  );
}

/** Every device of one owner: ``owner`` is "association" or the team's slug. */
export function OwnedDeviceList({
  devices,
  owner,
  queryKey,
  change,
}: {
  devices: OwnedDevices;
  owner: string;
  queryKey: QueryKey;
  change: (id: number, body: OwnedDeviceChange) => Promise<OwnedDevices>;
}) {
  if (!devices.items.length) return <EmptyState>No devices yet. An admin adds them.</EmptyState>;
  return (
    <Stack gap="lg">
      {devices.items.map((device) => (
        // Keyed by what was saved, so a card starts afresh after a save, not after every refresh.
        <DeviceCard
          key={JSON.stringify([device.id, device.name, device.place, device.description, device.settings])}
          device={device}
          owner={owner}
          queryKey={queryKey}
          change={change}
        />
      ))}
    </Stack>
  );
}
