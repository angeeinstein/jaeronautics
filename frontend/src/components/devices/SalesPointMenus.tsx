/**
 * An owner's sales points and what each sells (docs/todo.md, "Credit, sales
 * and devices"): on the owner's Sales page, under the price list the items
 * come from -- the association's on Admin › Sales, a team's on its Sales
 * page. Only the settings chosen from the price list (a sales point's menu,
 * up to four items in order); a device's name, place and switch are on its
 * device page. Data: the owner's devices (api/device_owners.py), saved with
 * the same PATCH, sending only these settings.
 */
import { Button, Group, Stack, Text, Title } from '@mantine/core';
import { type QueryKey, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError } from '../../api/client';
import {
  menuFields,
  type Settings,
  TypeSettingsFields,
  valuesOf,
} from '../../pages/admin/settings/deviceParts';
import { notifyDone, notifyFailed } from '../../lib/notify';
import { Panel } from '../Panel';
import type { OwnedDevice, OwnedDeviceChange, OwnedDevices } from './OwnedDevices';

function Menu({
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
  const fields = menuFields(device.fields);
  const [settings, setSettings] = useState<Settings>(device.settings);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => change(device.id, { settings: valuesOf(settings, fields) }),
    onSuccess: (data) => {
      setErrors({});
      client.setQueryData(queryKey, data);
      notifyDone(`${device.label}: saved.`);
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const nothingToChoose = fields.every((field) => !field.choices?.length);
  return (
    <Panel title={device.label}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <Stack gap="sm">
          {device.place ? (
            <Text size="sm" c="dimmed">
              {device.place}
            </Text>
          ) : null}
          <TypeSettingsFields
            settings={fields}
            owner={owner}
            values={settings}
            errors={errors}
            onChange={(field, value) => {
              setSettings({ ...settings, [field]: value });
            }}
          />
          {nothingToChoose ? (
            <Text size="sm" c="dimmed">
              Add items to the price list first.
            </Text>
          ) : null}
          <Group justify="flex-end">
            <Button type="submit" loading={save.isPending} disabled={nothingToChoose}>
              Save
            </Button>
          </Group>
        </Stack>
      </form>
    </Panel>
  );
}

/** The menus of one owner's sales points ("association" or a team's slug); none shown without one. */
export function SalesPointMenus({
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
  const points = devices.items.filter((device) => menuFields(device.fields).length);
  if (!points.length) return null;
  return (
    <Stack gap="sm">
      <Title order={2} size="h3">
        Sales points
      </Title>
      {points.map((device) => (
        <Menu key={device.id} device={device} owner={owner} queryKey={queryKey} change={change} />
      ))}
    </Stack>
  );
}
