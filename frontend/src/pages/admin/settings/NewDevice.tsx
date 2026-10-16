/**
 * Settings › Devices › Firmware, *New device* (docs/devices-plan.md, "A fresh
 * board flashed from the portal"): the factory image -- bootloader, partition
 * table and app in one file, one per chip -- and a board flashed with it over
 * USB, in the browser (Web Serial, Espressif's esptool-js, loaded only when a
 * board is flashed).
 *
 * The page finds the board's chip and refuses one there is no image for,
 * erases the whole flash first unless told not to (a used board then loses its
 * key and Wi-Fi), writes the image from address 0, checks it by its MD5, and
 * restarts the board. Pairing is a step of its own, on purpose: the board sets
 * up its Wi-Fi on its screen, then shows a code to pair it with.
 * Data: /api/v1/admin/devices/firmware/factory (api/device_firmware.py).
 */
import {
  Alert,
  Anchor,
  Button,
  Checkbox,
  Code,
  FileInput,
  Group,
  Progress,
  Stack,
  Table,
  Text,
} from '@mantine/core';
import { IconPackage, IconUsb } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';

import { api, ApiError, call, type Schemas } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Panel } from '../../../components/Panel';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { formatDateTime } from '../../../lib/format';
import { md5Hex } from '../../../lib/md5';
import { notifyDone, notifyFailed } from '../../../lib/notify';
import { firmwareKey, formatSize } from './deviceParts';

type FactoryOut = Schemas['FactoryImagesOut'];
type Image = Schemas['FactoryImageOut'];

export const factoryKey = [...firmwareKey, 'factory'] as const;

/** "ESP32-S3", as esptool-js names a chip, the way the portal does: "esp32s3". */
export function chipKey(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]/g, '');
}

/** "esp32s3" as people write it: "ESP32-S3". */
export function chipLabel(chip: string) {
  return chip.toUpperCase().replace(/^ESP32(?=[A-Z])/, 'ESP32-');
}

// --- The factory image ------------------------------------------------------------------------

function ImageRow({ image }: { image: Image }) {
  const client = useQueryClient();
  const remove = useMutation({
    mutationFn: () =>
      call(
        api.DELETE('/api/v1/admin/devices/firmware/factory/{image_id}', {
          params: { path: { image_id: image.id } },
        }),
      ),
    onSuccess: (data) => {
      client.setQueryData(factoryKey, data);
      notifyDone(`The factory image for the ${chipLabel(image.chip)} deleted.`);
    },
    onError: notifyFailed,
  });
  return (
    <Table.Tr>
      <Table.Td fw={600}>{chipLabel(image.chip)}</Table.Td>
      <Table.Td>
        <Text size="sm">{image.version}</Text>
        <Text size="xs" c="dimmed">
          {[image.built, image.idf_version].filter(Boolean).join(' · ')}
        </Text>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{formatSize(image.size)}</Text>
        <Text size="xs" c="dimmed">
          {image.file_name ?? ''}
        </Text>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{formatDateTime(image.uploaded_at)}</Text>
        <Text size="xs" c="dimmed">
          {image.uploaded_by ?? ''}
        </Text>
      </Table.Td>
      <Table.Td>
        <Group justify="flex-end">
          <ConfirmButton
            variant="default"
            size="xs"
            confirmLabel="Yes, delete"
            loading={remove.isPending}
            onConfirm={() => {
              remove.mutate();
            }}
          >
            Delete
          </ConfirmButton>
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}

function FactoryImages({ data }: { data: FactoryOut }) {
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const upload = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('Choose a file.');
      const form = new FormData();
      form.append('file', file);
      // openapi-fetch sends a FormData as it is, with its own Content-Type.
      return call(
        api.POST('/api/v1/admin/devices/firmware/factory', { body: form as unknown as { file: string } }),
      );
    },
    onSuccess: (result) => {
      setError(null);
      setFile(null);
      client.setQueryData(factoryKey, result);
      notifyDone('Factory image uploaded.');
    },
    onError: (failure) => {
      if (failure instanceof ApiError && failure.fields.file) setError(failure.fields.file);
      else notifyFailed(failure);
    },
  });
  return (
    <Panel icon={IconPackage} title="Factory image" flush>
      <Stack gap="md">
        {data.images.length ? (
          <Table.ScrollContainer minWidth={640} type="native">
            <Table verticalSpacing="sm" aria-label="Factory images">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Chip</Table.Th>
                  <Table.Th scope="col">Version</Table.Th>
                  <Table.Th scope="col">Size</Table.Th>
                  <Table.Th scope="col">Uploaded</Table.Th>
                  <Table.Th scope="col" aria-label="Actions" />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.images.map((image) => (
                  <ImageRow key={image.id} image={image} />
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ) : (
          <Stack px="md">
            <EmptyState>None uploaded yet.</EmptyState>
          </Stack>
        )}
        <Group align="flex-end" gap="md" px="md" pb="md">
          <FileInput
            label="Upload a factory image"
            description={`Bootloader, partition table and app in one file (idf.py merge-bin), written from address 0${
              data.project ? `, of “${data.project}”` : ''
            }. It replaces the one for its chip.`}
            placeholder="Choose a .bin file"
            accept=".bin,application/octet-stream"
            clearable
            value={file}
            error={error}
            onChange={(next) => {
              setFile(next);
              setError(null);
            }}
            flex="1 1 18rem"
          />
          <Button
            loading={upload.isPending}
            disabled={!file}
            onClick={() => {
              upload.mutate();
            }}
          >
            Upload
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

// --- Flashing a board -------------------------------------------------------------------------

type Phase = 'idle' | 'connecting' | 'erasing' | 'writing' | 'done' | 'failed';

const PHASE_TEXT: Partial<Record<Phase, string>> = {
  connecting: 'Connecting to the board…',
  erasing: 'Erasing the flash. This takes up to a minute…',
  writing: 'Writing…',
};

/** Why flashing stopped, in words. */
function failure(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  if (error instanceof DOMException && error.name === 'InvalidStateError')
    return 'The port is in use: close the serial monitor or IDE that has it open, then try again.';
  if (/Failed to connect|Timed? ?out|No serial data/i.test(message))
    return 'The board did not answer. Hold its BOOT button while plugging it in, then try again.';
  return message;
}

function Flasher({ images }: { images: Image[] }) {
  const [erase, setErase] = useState(true);
  const [phase, setPhase] = useState<Phase>('idle');
  const [board, setBoard] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const busy = phase === 'connecting' || phase === 'erasing' || phase === 'writing';
  const closing = useRef<(() => Promise<void>) | null>(null);

  // A board left open when the page is: the port closed.
  useEffect(
    () => () => {
      void closing.current?.();
    },
    [],
  );

  if (!('serial' in navigator)) {
    return (
      <Alert color="amber" variant="light">
        This browser cannot reach a board over USB. Use Chrome or Edge on a computer.
      </Alert>
    );
  }
  if (!images.length) {
    return <Text c="dimmed">Upload a factory image first.</Text>;
  }

  const flash = async () => {
    setProblem(null);
    setBoard(null);
    setProgress(0);
    setLog([]);
    let port: SerialPort;
    try {
      port = await navigator.serial.requestPort();
    } catch {
      return; // No board chosen.
    }
    setPhase('connecting');
    const { ESPLoader, Transport } = await import('esptool-js');
    const transport = new Transport(port, false);
    closing.current = () => transport.disconnect().catch(() => undefined);
    const line = (text: string) => {
      setLog((lines) => [...lines.slice(-200), text]);
    };
    try {
      const loader = new ESPLoader({
        transport,
        baudrate: 921600,
        romBaudrate: 115200,
        terminal: { clean: () => undefined, writeLine: line, write: line },
      });
      await loader.main();
      const chip = chipKey(loader.chip.CHIP_NAME);
      const mac = await loader.chip.readMac(loader);
      setBoard(`${loader.chip.CHIP_NAME} · ${mac.toUpperCase()}`);
      const image = images.find((candidate) => candidate.chip === chip);
      if (!image) {
        throw new Error(
          `This board is an ${loader.chip.CHIP_NAME}; there is no factory image for it. Images: ${images
            .map((candidate) => chipLabel(candidate.chip))
            .join(', ')}.`,
        );
      }
      const { data } = await api.GET('/api/v1/admin/devices/firmware/factory/{image_id}/file', {
        params: { path: { image_id: image.id } },
        parseAs: 'arrayBuffer',
      });
      if (!(data instanceof ArrayBuffer)) throw new Error('The factory image could not be loaded.');
      setPhase(erase ? 'erasing' : 'writing');
      await loader.writeFlash({
        fileArray: [{ data: new Uint8Array(data), address: 0 }],
        flashMode: 'keep',
        flashFreq: 'keep',
        flashSize: 'keep',
        eraseAll: erase,
        compress: true,
        reportProgress: (_file, written, total) => {
          setPhase('writing');
          setProgress(total ? Math.round((written / total) * 100) : 0);
        },
        calculateMD5Hash: md5Hex,
      });
      await loader.after('hard_reset');
      setPhase('done');
    } catch (error) {
      setProblem(failure(error));
      setPhase('failed');
    } finally {
      await transport.disconnect().catch(() => undefined);
      closing.current = null;
    }
  };

  return (
    <Stack gap="md">
      <Text size="sm" c="dimmed">
        Connect the board by USB and choose its port. A board that is not found: hold its BOOT button while
        plugging it in.
      </Text>
      <Checkbox
        label="Erase everything first"
        description="Also clears a used board’s key and Wi-Fi: it is then paired again."
        checked={erase}
        disabled={busy}
        onChange={(event) => {
          setErase(event.currentTarget.checked);
        }}
      />
      <Group>
        <Button
          leftSection={<IconUsb size={16} />}
          loading={busy}
          onClick={() => {
            void flash();
          }}
        >
          {phase === 'done' || phase === 'failed' ? 'Flash another board' : 'Connect and flash'}
        </Button>
        {board ? (
          <Text size="sm" c="dimmed">
            {board}
          </Text>
        ) : null}
      </Group>
      {busy ? (
        <Stack gap={6} role="status">
          <Text size="sm">{PHASE_TEXT[phase]}</Text>
          {phase === 'writing' ? <Progress value={progress} aria-label="Written" /> : null}
        </Stack>
      ) : null}
      {phase === 'done' ? (
        <Alert color="green" variant="light" role="status" title="Flashed and checked">
          The board restarts. Set up its Wi-Fi on its screen; it then shows a code to{' '}
          <Anchor component={Link} to="/admin/settings/devices/pair">
            pair it
          </Anchor>
          .
        </Alert>
      ) : null}
      {problem ? (
        <Alert color="red" variant="light" role="alert" title="Not flashed">
          {problem}
        </Alert>
      ) : null}
      {log.length ? (
        <details>
          <summary>
            <Text component="span" size="sm" c="dimmed">
              What the flasher said
            </Text>
          </summary>
          <Code block mt="xs" mah={240} style={{ overflow: 'auto' }}>
            {log.join('\n')}
          </Code>
        </details>
      ) : null}
    </Stack>
  );
}

export function NewDevice() {
  const loaded = useQuery({
    queryKey: factoryKey,
    queryFn: () => call(api.GET('/api/v1/admin/devices/firmware/factory')),
  });
  if (loaded.isPending) return <LoadingState />;
  if (loaded.isError) return <ErrorState error={loaded.error} onRetry={() => void loaded.refetch()} />;
  return (
    <Stack gap="lg">
      <Panel icon={IconUsb} title="Flash a board">
        <Flasher images={loaded.data.images} />
      </Panel>
      <FactoryImages data={loaded.data} />
    </Stack>
  );
}
