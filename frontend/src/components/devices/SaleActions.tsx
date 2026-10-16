/**
 * What a sales point switches after a sale (docs/devices-plan.md, "Actions
 * after a sale"): its outputs -- a fridge's lock, a coffee machine's relay --
 * for some seconds, after any sale or after one item of its menu. One list of
 * lines per reader, kept in its settings ("actions"); the reader is told what
 * to switch with each sale. Behind a button on the reader's panel, shown only
 * when the reader says it has outputs, so the Sales page stays plain.
 */
import {
  ActionIcon,
  Alert,
  Button,
  Group,
  Modal,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
} from '@mantine/core';
import { IconPlus, IconTrash } from '@tabler/icons-react';
import { type QueryKey, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError } from '../../api/client';
import { notifyDone, notifyFailed } from '../../lib/notify';
import type { OwnedDevice, OwnedDeviceChange, OwnedDevices } from './OwnedDevices';

/** One line: after any sale (``item`` null) or after this item, switch ``output`` for ``seconds``. */
export interface ActionLine {
  item: number | null;
  output: number;
  seconds: number;
}

export interface Actions {
  on: boolean;
  lines: ActionLine[];
}

/** A line while it is edited: its seconds as typed ("0." on the way to "0.5"). */
type EditedLine = Omit<ActionLine, 'seconds'> & { seconds: number | string };

/** As the server keeps it, at most this many lines. */
const MOST = 16;
const ANY = 'any';

/** A reader's actions as kept, or none yet. */
export function actionsOf(device: OwnedDevice): Actions {
  const kept = device.settings.actions as Partial<Actions> | undefined;
  return { on: kept?.on ?? true, lines: Array.isArray(kept?.lines) ? kept.lines : [] };
}

/** Its menu's items, by name: what a line may be for besides any sale. */
function menuOf(device: OwnedDevice): { value: string; label: string }[] {
  const field = device.fields.find((setting) => setting.name === 'items');
  const chosen = Array.isArray(device.settings.items) ? (device.settings.items as unknown[]) : [];
  return chosen.flatMap((id) => {
    const choice = field?.choices?.find((item) => item.value === id);
    return choice ? [{ value: String(id), label: choice.label.split(', ')[0] ?? choice.label }] : [];
  });
}

export function SaleActions({
  device,
  queryKey,
  change,
}: {
  device: OwnedDevice;
  queryKey: QueryKey;
  change: (id: number, body: OwnedDeviceChange) => Promise<OwnedDevices>;
}) {
  const [opened, setOpened] = useState(false);
  const kept = actionsOf(device);
  const count = kept.on ? kept.lines.length : 0;
  return (
    <>
      <Button
        variant="default"
        size="compact-sm"
        onClick={() => {
          setOpened(true);
        }}
      >
        {count ? `Actions · ${String(count)}` : 'Actions'}
      </Button>
      {opened ? (
        <ActionsDialog
          device={device}
          queryKey={queryKey}
          change={change}
          onClose={() => {
            setOpened(false);
          }}
        />
      ) : null}
    </>
  );
}

function ActionsDialog({
  device,
  queryKey,
  change,
  onClose,
}: {
  device: OwnedDevice;
  queryKey: QueryKey;
  change: (id: number, body: OwnedDeviceChange) => Promise<OwnedDevices>;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [actions, setActions] = useState<{ on: boolean; lines: EditedLine[] }>(() => actionsOf(device));
  const outputs = Array.from({ length: device.outputs ?? 0 }, (_, index) => String(index + 1));
  const when = [{ value: ANY, label: 'Any sale' }, ...menuOf(device)];
  const save = useMutation({
    mutationFn: () =>
      change(device.id, {
        settings: {
          actions: {
            on: actions.on,
            lines: actions.lines.map((line) => ({ ...line, seconds: Number(line.seconds) })),
          },
        },
      }),
    onSuccess: (data) => {
      client.setQueryData(queryKey, data);
      notifyDone(`${device.label}: actions saved.`);
      onClose();
    },
    onError: (error) => {
      if (!(error instanceof ApiError && Object.keys(error.fields).length)) notifyFailed(error);
    },
  });
  const refused = save.error instanceof ApiError ? Object.values(save.error.fields)[0] : undefined;
  const setLine = (index: number, line: Partial<EditedLine>) => {
    setActions({
      ...actions,
      lines: actions.lines.map((current, at) => (at === index ? { ...current, ...line } : current)),
    });
  };
  return (
    <Modal opened onClose={onClose} title={`${device.label}: actions`} size="lg" centered>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            After a sale, switch an output for some seconds: after any sale, or after one item.
          </Text>
          <Switch
            label="On"
            description="Off: kept, but nothing is switched."
            checked={actions.on}
            onChange={(event) => {
              setActions({ ...actions, on: event.currentTarget.checked });
            }}
          />
          {actions.lines.length ? (
            <Stack gap="xs" aria-label="Actions" role="list">
              {actions.lines.map((line, index) => (
                <Group key={index} gap="xs" align="flex-end" wrap="nowrap" role="listitem">
                  <Select
                    label={index ? undefined : 'After'}
                    aria-label="After"
                    data={when}
                    value={line.item === null ? ANY : String(line.item)}
                    allowDeselect={false}
                    flex={1}
                    onChange={(value) => {
                      setLine(index, { item: value === null || value === ANY ? null : Number(value) });
                    }}
                  />
                  <Select
                    label={index ? undefined : 'Output'}
                    aria-label="Output"
                    data={outputs}
                    value={String(line.output)}
                    allowDeselect={false}
                    w={90}
                    onChange={(value) => {
                      setLine(index, { output: Number(value ?? 1) });
                    }}
                  />
                  <NumberInput
                    label={index ? undefined : 'Seconds'}
                    aria-label="Seconds"
                    min={0.1}
                    max={600}
                    step={0.5}
                    decimalScale={1}
                    value={line.seconds}
                    w={110}
                    onChange={(value) => {
                      setLine(index, { seconds: value });
                    }}
                  />
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    size="lg"
                    aria-label="Remove"
                    onClick={() => {
                      setActions({ ...actions, lines: actions.lines.filter((_, at) => at !== index) });
                    }}
                  >
                    <IconTrash size={16} />
                  </ActionIcon>
                </Group>
              ))}
            </Stack>
          ) : (
            <Text size="sm" c="dimmed">
              Nothing is switched after a sale yet.
            </Text>
          )}
          {refused ? (
            <Alert color="red" variant="light">
              {refused}
            </Alert>
          ) : null}
          <Group justify="space-between">
            <Button
              variant="default"
              leftSection={<IconPlus size={16} />}
              disabled={actions.lines.length >= MOST}
              onClick={() => {
                setActions({ ...actions, lines: [...actions.lines, { item: null, output: 1, seconds: 5 }] });
              }}
            >
              Add
            </Button>
            <Group gap="xs">
              <Button variant="default" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" loading={save.isPending}>
                Save
              </Button>
            </Group>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
