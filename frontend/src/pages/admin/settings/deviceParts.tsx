/**
 * What the device pages share (docs/devices-plan.md): a device's state in one
 * word, the fields of its type's settings, and its key -- shown the one time
 * it can be, to copy or download as the device's configuration.
 */
import {
  Alert,
  Button,
  Code,
  CopyButton,
  Group,
  NumberInput,
  Stack,
  Switch,
  Text,
  TextInput,
} from '@mantine/core';

import type { Schemas } from '../../../api/client';
import { Panel } from '../../../components/Panel';
import type { Tone } from '../../../components/Pill';
import { formatDateTime } from '../../../lib/format';
import { saveFile } from '../../../lib/files';

export type Device = Schemas['DeviceOut'];
export type DeviceType = Schemas['DeviceTypeOut'];
export type DeviceKey = Schemas['DeviceKeyOut'];
export type Settings = Record<string, unknown>;

export const devicesKey = ['admin', 'devices'] as const;

/** Off, online (spoke in the last five minutes), seen before, or never. */
export function statusOf(device: Device): { tone: Tone; label: string; detail: string } {
  if (!device.enabled) return { tone: 'neutral', label: 'Off', detail: 'Switched off: its key is refused.' };
  if (device.online) return { tone: 'active', label: 'Online', detail: 'Spoke in the last five minutes.' };
  if (device.last_seen_at)
    return { tone: 'pending', label: 'Quiet', detail: `Last seen ${formatDateTime(device.last_seen_at)}.` };
  return { tone: 'neutral', label: 'New', detail: 'Has not spoken yet.' };
}

/** Each setting of a type, as a field of its kind. */
export function TypeSettingsFields({
  type,
  values,
  errors,
  onChange,
}: {
  type: DeviceType;
  values: Settings;
  errors: Record<string, string>;
  onChange: (name: string, value: unknown) => void;
}) {
  if (!type.settings.length) return null;
  return (
    <Stack gap="sm">
      {type.settings.map((setting) => {
        const error = errors[`settings.${setting.name}`] ?? null;
        const value = values[setting.name] ?? setting.default;
        if (setting.kind === 'switch')
          return (
            <Switch
              key={setting.name}
              label={setting.label}
              description={setting.help || undefined}
              checked={value === true}
              error={error}
              onChange={(event) => {
                onChange(setting.name, event.currentTarget.checked);
              }}
            />
          );
        if (setting.kind === 'number')
          return (
            <NumberInput
              key={setting.name}
              label={setting.label}
              description={setting.help || undefined}
              allowDecimal={false}
              min={setting.minimum ?? undefined}
              max={setting.maximum ?? undefined}
              value={typeof value === 'number' ? value : ''}
              error={error}
              onChange={(next) => {
                onChange(setting.name, typeof next === 'number' ? next : null);
              }}
            />
          );
        return (
          <TextInput
            key={setting.name}
            label={setting.label}
            description={setting.help || undefined}
            maxLength={setting.max_length}
            value={typeof value === 'string' ? value : ''}
            error={error}
            onChange={(event) => {
              onChange(setting.name, event.currentTarget.value);
            }}
          />
        );
      })}
    </Stack>
  );
}

function fileName(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
  return `device-${slug || 'configuration'}.json`;
}

/** A key, the one time it can be read. */
export function KeyShown({ result, onDone }: { result: DeviceKey; onDone: () => void }) {
  return (
    <Panel title={`The key of ${result.device.name}`}>
      <Stack gap="md">
        <Alert color="amber" variant="light">
          Shown this once: the portal keeps only a hash of it. Put it on the device now. If it is lost, give
          the device a new key.
        </Alert>
        <Code block style={{ wordBreak: 'break-all', whiteSpace: 'pre-wrap' }} data-testid="device-key">
          {result.key}
        </Code>
        <Group gap="sm">
          <CopyButton value={result.key}>
            {({ copied, copy }) => (
              <Button variant="default" onClick={copy}>
                {copied ? 'Copied' : 'Copy key'}
              </Button>
            )}
          </CopyButton>
          <Button
            variant="default"
            onClick={() => {
              const file = new Blob([`${JSON.stringify(result.configuration, null, 2)}\n`], {
                type: 'application/json',
              });
              saveFile(file, fileName(result.device.name));
            }}
          >
            Download configuration
          </Button>
          <Button onClick={onDone}>Done</Button>
        </Group>
        <Text size="sm" c="dimmed">
          The configuration file holds the portal&apos;s address, the device&apos;s number, its type and this
          key.
        </Text>
      </Stack>
    </Panel>
  );
}
