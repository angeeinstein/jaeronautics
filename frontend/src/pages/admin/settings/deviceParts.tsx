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
  MultiSelect,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
} from '@mantine/core';
import { IconKey } from '@tabler/icons-react';

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
export const firmwareKey = ['admin', 'devices', 'firmware'] as const;

export type Pairing = Schemas['DevicePairingOut'];
export type FirmwareState = Device['firmware_state'];

/** The types a device's firmware can be; every type when it has not said. */
export function typesFor(types: DeviceType[], supported: string[] | null | undefined) {
  return supported ? types.filter((type) => supported.includes(type.slug)) : types;
}

/** What a firmware can be used as, in words. */
export function canBe(types: DeviceType[], supported: string[] | null | undefined) {
  if (!supported) return 'Not said: any type';
  if (!supported.length) return 'Nothing this portal knows';
  return supported.map((slug) => types.find((type) => type.slug === slug)?.label ?? slug).join(', ');
}

/** How a device's firmware stands, in a word. */
export const FIRMWARE_STATE: Record<FirmwareState, { tone: Tone; label: string }> = {
  none: { tone: 'neutral', label: 'No update chosen' },
  current: { tone: 'active', label: 'Up to date' },
  waiting: { tone: 'pending', label: 'Update waiting' },
  failed: { tone: 'failed', label: 'Update failed' },
};

/** A size in bytes, for people: "1.4 MB". */
export function formatSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${String(Math.max(1, Math.round(bytes / 1024)))} KB`;
}

/** Off, online (spoke in the last five minutes), seen before, or never. */
export function statusOf(device: Device): { tone: Tone; label: string; detail: string } {
  if (!device.enabled) return { tone: 'neutral', label: 'Off', detail: 'Switched off: its key is refused.' };
  if (device.key_revoked_at)
    return { tone: 'neutral', label: 'No key', detail: 'Its key was revoked: it cannot sign in.' };
  if (device.online) return { tone: 'active', label: 'Online', detail: 'Spoke in the last five minutes.' };
  if (device.last_seen_at)
    return { tone: 'pending', label: 'Quiet', detail: `Last seen ${formatDateTime(device.last_seen_at)}.` };
  return { tone: 'neutral', label: 'New', detail: 'Has not spoken yet.' };
}

export type SettingField = DeviceType['settings'][number];

/** The settings a device's own pages show: the technical ones. Those chosen from the
 * owner's price list (a sales point's menu) are set on the owner's Sales page. */
export function deviceFields(settings: SettingField[]): SettingField[] {
  return settings.filter((setting) => !setting.with_prices);
}

/** The settings set with the owner's price list, on their Sales page. */
export function menuFields(settings: SettingField[]): SettingField[] {
  return settings.filter((setting) => setting.with_prices);
}

/** Only these settings' values, those given: what a form sends is what it shows. */
export function valuesOf(values: Settings, fields: SettingField[]): Settings {
  return Object.fromEntries(
    fields.filter((field) => field.name in values).map((field) => [field.name, values[field.name]]),
  );
}

/** Where an owner ("association" or a team's slug) chooses its devices' menus. */
export function salesPageOf(owner: string): string {
  return owner === 'association' ? '/admin/sales' : `/teams/${owner}/manage/sales`;
}

/**
 * Each setting, as a field of its kind. A choice offers what ``owner`` (the
 * association, or a team's slug) may choose: a reader sells its owner's items.
 */
export function TypeSettingsFields({
  settings,
  owner,
  values,
  errors,
  onChange,
}: {
  settings: SettingField[];
  owner: string;
  values: Settings;
  errors: Record<string, string>;
  onChange: (name: string, value: unknown) => void;
}) {
  if (!settings.length) return null;
  return (
    <Stack gap="sm">
      {settings.map((setting) => {
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
        if (setting.kind === 'choices') {
          // Several, in the order chosen: the order is the device's (its menu's).
          const choices = (setting.choices ?? []).filter((choice) => choice.owner === owner);
          const groups = [...new Set(choices.map((choice) => choice.group))];
          const data = groups.map((group) => ({
            group: group || setting.label,
            items: choices
              .filter((choice) => choice.group === group)
              .map((choice) => ({ value: JSON.stringify(choice.value), label: choice.label })),
          }));
          const known = new Set(choices.map((choice) => JSON.stringify(choice.value)));
          const chosen = (Array.isArray(value) ? value : []).map((item) => JSON.stringify(item));
          const gone = chosen.filter((item) => !known.has(item)).length;
          return (
            <MultiSelect
              key={setting.name}
              label={setting.label}
              description={setting.help || undefined}
              placeholder={
                !choices.length ? 'Nothing to choose yet' : chosen.length ? undefined : 'Choose, in order'
              }
              data={data}
              value={chosen.filter((item) => known.has(item))}
              maxValues={setting.most ?? undefined}
              error={
                error ??
                (gone ? `${String(gone)} no longer on sale: it is left out until chosen again.` : null)
              }
              searchable
              nothingFoundMessage="Nothing found"
              onChange={(next) => {
                onChange(
                  setting.name,
                  next.map((item) => JSON.parse(item) as unknown),
                );
              }}
            />
          );
        }
        if (setting.kind === 'choice') {
          const choices = (setting.choices ?? []).filter((choice) => choice.owner === owner);
          // Grouped under their heading (an item's seller); the value kept as the server gave it.
          const groups = [...new Set(choices.map((choice) => choice.group))];
          const data = groups.map((group) => ({
            group: group || setting.label,
            items: choices
              .filter((choice) => choice.group === group)
              .map((choice) => ({ value: JSON.stringify(choice.value), label: choice.label })),
          }));
          const current = value === null || value === undefined ? null : JSON.stringify(value);
          const known = choices.some((choice) => JSON.stringify(choice.value) === current);
          return (
            <Select
              key={setting.name}
              label={setting.label}
              description={setting.help || undefined}
              placeholder={choices.length ? 'Choose' : 'Nothing to choose yet'}
              data={data}
              value={known ? current : null}
              error={error ?? (current !== null && !known ? 'Not on sale: choose another.' : null)}
              clearable
              searchable
              nothingFoundMessage="Nothing found"
              onChange={(next) => {
                onChange(setting.name, next === null ? null : (JSON.parse(next) as unknown));
              }}
            />
          );
        }
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

/**
 * A key, the one time it can be read: to copy, or to download as the
 * device's configuration, or as the starter for its firmware -- everything
 * firmware for it needs, with an example sketch (a Markdown file).
 */
export function KeyReveal({ result, onDone }: { result: DeviceKey; onDone: () => void }) {
  return (
    <Stack gap="md">
      <Alert color="amber" variant="light">
        Shown this once: the portal keeps only a hash of it. Put it on the device now. If it is lost, rotate
        the key.
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
            saveFile(file, `device-${result.device.code}.json`);
          }}
        >
          Download configuration
        </Button>
        <Button
          variant="default"
          onClick={() => {
            saveFile(new Blob([result.starter], { type: 'text/markdown' }), result.starter_name);
          }}
        >
          Download starter
        </Button>
        <Button onClick={onDone}>Done</Button>
      </Group>
      <Text size="sm" c="dimmed">
        The configuration holds the portal&apos;s address, the device&apos;s code, its type and this key. The
        starter adds every request the device makes and an example sketch for an ESP32, to build its firmware
        from.
      </Text>
    </Stack>
  );
}

/** A new device's key, where the form to add it was. */
export function KeyShown({ result, onDone }: { result: DeviceKey; onDone: () => void }) {
  return (
    <Panel icon={IconKey} title={`The key of ${result.device.label}`}>
      <KeyReveal result={result} onDone={onDone} />
    </Panel>
  );
}
