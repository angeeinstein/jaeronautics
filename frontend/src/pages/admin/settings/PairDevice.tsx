/**
 * Settings -> Devices -> Pair: a device asking to be paired shows a code and
 * a QR code to this page with the code filled in (docs/devices-plan.md,
 * "Pairing"). Here an admin sees what the device says it is and says what it
 * becomes: a new device (owner, type, name, place, settings) or the device it
 * replaces, whose old board stops at once. Or turns it away. The device then
 * fetches its key itself; nobody sees it. Data: /api/v1/admin/devices and
 * /api/v1/admin/devices/pairings/<code> (aeronautics_members/api/device_pairings.py).
 */
import { Button, Group, SegmentedControl, Select, SimpleGrid, Stack, Text, TextInput } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';

import { api, ApiError, call, type Schemas } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Details } from '../../../components/Details';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { ErrorState, LoadingState } from '../../../components/States';
import { formatDateTime } from '../../../lib/format';
import { notifyDone, notifyFailed } from '../../../lib/notify';
import { canBe, devicesKey, type Pairing, type Settings, TypeSettingsFields, typesFor } from './deviceParts';

type Devices = Schemas['DevicesOut'];

/** Asking for the code shown on the device. */
function EnterCode({ initial, onFound }: { initial: string; onFound: (code: string) => void }) {
  const [code, setCode] = useState(initial);
  return (
    <Panel title="The code on the device">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (code.trim()) onFound(code.trim().toUpperCase());
        }}
      >
        <Group align="flex-end" gap="sm">
          <TextInput
            label="Code"
            description="Shown on the device's screen, with a QR code that opens this page."
            placeholder="K7Q-4X9"
            maxLength={9}
            value={code}
            styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)' } }}
            onChange={(event) => {
              setCode(event.currentTarget.value);
            }}
          />
          <Button type="submit" disabled={!code.trim()}>
            Find
          </Button>
        </Group>
      </form>
    </Panel>
  );
}

function Approve({ pairing, devices }: { pairing: Pairing; devices: Devices }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const types = typesFor(devices.types, pairing.supported_types);
  const [mode, setMode] = useState<'new' | 'replaces'>('new');
  const [owner, setOwner] = useState('association');
  const [typeSlug, setTypeSlug] = useState<string | null>(
    types.length === 1 ? (types[0]?.slug ?? null) : null,
  );
  const [name, setName] = useState('');
  const [place, setPlace] = useState('');
  const [settings, setSettings] = useState<Settings>({});
  const [replaces, setReplaces] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const type = types.find((item) => item.slug === typeSlug);
  const path = { params: { path: { code: pairing.code } } };
  const done = async () => {
    await client.invalidateQueries({ queryKey: devicesKey, exact: true });
  };
  const pair = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/admin/devices/pairings/{code}', {
          ...path,
          body:
            mode === 'replaces'
              ? { replaces: Number(replaces) }
              : { type: typeSlug ?? '', owner, name: name || null, place, settings },
        }),
      ),
    onSuccess: async (device) => {
      notifyDone(`${device.label} is paired: it fetches its key now.`);
      await navigate(`/admin/settings/devices/${String(device.id)}`);
      await done();
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const refuse = useMutation({
    mutationFn: () => call(api.DELETE('/api/v1/admin/devices/pairings/{code}', path)),
    onSuccess: async () => {
      notifyDone('Turned away: the device is told so.');
      await navigate('/admin/settings/devices');
      await done();
    },
    onError: notifyFailed,
  });
  // A device it may replace: one its firmware can be.
  const replaceable = devices.items.filter(
    (device) => !pairing.supported_types || pairing.supported_types.includes(device.type),
  );

  return (
    <Stack gap="lg">
      <Panel title={`Device ${pairing.code}`}>
        <Details
          items={[
            ['Board', [pairing.board, pairing.hardware_id].filter(Boolean).join(' · ') || '–'],
            ['Firmware', pairing.firmware ?? '–'],
            ['Can be', canBe(devices.types, pairing.supported_types)],
            [
              'Asked',
              `${formatDateTime(pairing.created_at)}${pairing.address ? ` from ${pairing.address}` : ''}`,
            ],
            ['Code works until', formatDateTime(pairing.expires_at)],
          ]}
        />
      </Panel>
      <Panel title="It becomes">
        <Stack gap="md">
          <SegmentedControl
            aria-label="It becomes"
            value={mode}
            data={[
              { value: 'new', label: 'A new device' },
              { value: 'replaces', label: 'The board of a device' },
            ]}
            onChange={(value) => {
              setMode(value === 'replaces' ? 'replaces' : 'new');
              setErrors({});
            }}
          />
          {mode === 'replaces' ? (
            <Select
              label="Device"
              description="It moves to this board, with its settings and history; the old board's key stops at once."
              placeholder="Choose"
              data={replaceable.map((device) => ({
                value: String(device.id),
                label: `${device.label} · ${device.type_label} · ${device.owner_label}`,
              }))}
              value={replaces}
              searchable
              nothingFoundMessage="Nothing found"
              onChange={setReplaces}
            />
          ) : (
            <>
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                <Select
                  label="Owner"
                  description="Its owner names it and chooses what it does."
                  data={devices.owners}
                  value={owner}
                  error={errors.owner ?? null}
                  allowDeselect={false}
                  onChange={(next) => {
                    setOwner(next ?? 'association');
                    setSettings({});
                  }}
                />
                <Select
                  label="Type"
                  placeholder={types.length ? 'Choose' : 'Its firmware can be nothing this portal knows'}
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
                  label="Name (optional)"
                  description="Its code stands in for it."
                  maxLength={80}
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
              {type ? (
                <TypeSettingsFields
                  settings={type.settings}
                  owner={owner}
                  values={settings}
                  errors={errors}
                  onChange={(field, value) => {
                    setSettings({ ...settings, [field]: value });
                  }}
                />
              ) : null}
            </>
          )}
          <Group justify="space-between" gap="sm">
            <ConfirmButton
              variant="default"
              confirmLabel="Yes, turn it away"
              loading={refuse.isPending}
              onConfirm={() => {
                refuse.mutate();
              }}
            >
              Turn away
            </ConfirmButton>
            <Button
              loading={pair.isPending}
              disabled={mode === 'replaces' ? !replaces : !typeSlug}
              onClick={() => {
                pair.mutate();
              }}
            >
              Pair
            </Button>
          </Group>
        </Stack>
      </Panel>
    </Stack>
  );
}

function Found({ code, devices, onOther }: { code: string; devices: Devices; onOther: () => void }) {
  const pairing = useQuery({
    queryKey: [...devicesKey, 'pairing', code],
    queryFn: () => call(api.GET('/api/v1/admin/devices/pairings/{code}', { params: { path: { code } } })),
    retry: false,
    // Read once: answered, the pairing is gone, and asking again would only be "not found".
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  if (pairing.isPending) return <LoadingState />;
  if (pairing.isError)
    return (
      <Stack gap="md">
        <ErrorState error={pairing.error} />
        <Group>
          <Button variant="default" onClick={onOther}>
            Enter another code
          </Button>
        </Group>
      </Stack>
    );
  return <Approve pairing={pairing.data} devices={devices} />;
}

export function PairDevice() {
  const [params, setParams] = useSearchParams();
  const code = params.get('code') ?? '';
  const devices = useQuery({ queryKey: devicesKey, queryFn: () => call(api.GET('/api/v1/admin/devices')) });
  return (
    <>
      <PageHeader
        title="Pair a device"
        description="Approve a device by the code on its screen. It fetches its key itself."
        crumbs={[
          { label: 'Admin', to: '/admin' },
          { label: 'Settings' },
          { label: 'Devices', to: '/admin/settings/devices' },
          { label: 'Pair' },
        ]}
      />
      {devices.isPending ? (
        <LoadingState />
      ) : devices.isError ? (
        <ErrorState error={devices.error} onRetry={() => void devices.refetch()} />
      ) : code ? (
        <Found
          key={code}
          code={code}
          devices={devices.data}
          onOther={() => {
            setParams({});
          }}
        />
      ) : (
        <Stack gap="lg">
          <EnterCode
            initial=""
            onFound={(next) => {
              setParams({ code: next });
            }}
          />
          {devices.data.pairings.length ? (
            <Text size="sm" c="dimmed">
              Waiting now:{' '}
              {devices.data.pairings.map((pairing, index) => (
                <span key={pairing.code}>
                  {index ? ', ' : ''}
                  <Link to={`?code=${encodeURIComponent(pairing.code)}`}>{pairing.code}</Link>
                </span>
              ))}
            </Text>
          ) : null}
        </Stack>
      )}
    </>
  );
}
