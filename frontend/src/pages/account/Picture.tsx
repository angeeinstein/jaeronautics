/**
 * The forum's profile picture: choose a photo, frame it -- drag it, zoom
 * it, or use the arrow keys -- and send it for review. The page draws the
 * square the server will cut (lib/crop.ts), so what is seen is what is sent;
 * the round mask shows how the forum will show it. The photo itself is sent
 * whole, with the square, and checked and cut by the server.
 */
import { ActionIcon, Alert, Button, FileButton, Group, Slider, Stack, Text } from '@mantine/core';
import { IconMinus, IconPlus } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { api, ApiError, call, type Schemas } from '../../api/client';
import { type Crop, drag, MAX_ZOOM, MIN_ZOOM, settle, square, START } from '../../lib/crop';
import { notifyDone, notifyFailed } from '../../lib/notify';
import classes from './Picture.module.css';
import { accountKey } from './shared';

type Picture = Schemas['PictureOut'];

const ACCEPT = 'image/jpeg,image/png,image/webp,image/avif';
const STEP = 0.02;

/** The preview's width on screen; 320 before it has one (not yet laid out). */
function sizeOf(element: HTMLCanvasElement | null): number {
  const width = element?.clientWidth ?? 0;
  return width > 0 ? width : 320;
}

function Cropper({ image, crop, onCrop }: { image: ImageBitmap; crop: Crop; onCrop: (crop: Crop) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const last = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext('2d');
    if (!element || !context) return;
    const pixels = Math.round(sizeOf(element) * Math.max(1, window.devicePixelRatio));
    element.width = pixels;
    element.height = pixels;
    const { left, top, side } = square(image.width, image.height, crop);
    context.clearRect(0, 0, pixels, pixels);
    context.drawImage(image, left, top, side, side, 0, 0, pixels, pixels);
  }, [image, crop]);

  const move = (dx: number, dy: number) => {
    onCrop(drag(image.width, image.height, crop, dx, dy, sizeOf(canvas.current)));
  };

  return (
    <div
      className={classes.stage}
      role="application"
      aria-label="Your photo, as it will be cut. Drag it, or use the arrow keys, to move it."
      tabIndex={0}
      onPointerDown={(event) => {
        last.current = { x: event.clientX, y: event.clientY };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        if (!last.current) return;
        move(event.clientX - last.current.x, event.clientY - last.current.y);
        last.current = { x: event.clientX, y: event.clientY };
      }}
      onPointerUp={() => {
        last.current = null;
      }}
      onPointerCancel={() => {
        last.current = null;
      }}
      onKeyDown={(event) => {
        const by = sizeOf(canvas.current) * STEP * 2;
        const moves: Record<string, [number, number]> = {
          ArrowLeft: [by, 0],
          ArrowRight: [-by, 0],
          ArrowUp: [0, by],
          ArrowDown: [0, -by],
        };
        const step = moves[event.key];
        if (!step) return;
        event.preventDefault();
        move(step[0], step[1]);
      }}
    >
      <canvas ref={canvas} className={classes.canvas} />
      <div className={classes.mask} aria-hidden="true" />
    </div>
  );
}

export function PictureUpload({ picture, onDone }: { picture: Picture; onDone?: () => void }) {
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [image, setImage] = useState<ImageBitmap | null>(null);
  const [crop, setCrop] = useState<Crop>(START);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(
    () => () => {
      image?.close();
    },
    [image],
  );

  const choose = async (chosen: File | null) => {
    setProblem(null);
    setImage(null);
    setFile(null);
    if (!chosen) return;
    if (chosen.size > picture.max_bytes) {
      setProblem(`This file is too large. Please keep it below ${picture.max_label}.`);
      return;
    }
    try {
      const decoded = await createImageBitmap(chosen);
      setFile(chosen);
      setImage(decoded);
      setCrop(settle(decoded.width, decoded.height, START));
    } catch {
      setProblem('This file could not be read as a picture. Please choose a JPG, PNG, WebP or AVIF photo.');
    }
  };

  const send = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('Choose a photo first.');
      const form = new FormData();
      form.append('image', file);
      return call(
        api.POST('/api/v1/account/picture', {
          params: { query: { zoom: crop.zoom, x: crop.x, y: crop.y } },
          body: form as unknown as { image: string },
        }),
      );
    },
    onSuccess: async () => {
      notifyDone('Thanks! Your picture is waiting for review. We will email you once it is approved.');
      setFile(null);
      setImage(null);
      await client.invalidateQueries({ queryKey: accountKey });
      onDone?.();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.fields.image) setProblem(error.fields.image);
      else notifyFailed(error);
    },
  });

  const zoomBy = (delta: number) => {
    if (image) setCrop(settle(image.width, image.height, { ...crop, zoom: crop.zoom + delta }));
  };

  return (
    <Stack gap="sm">
      <Text size="sm">A real photo of you, any pose. An admin looks at it before the forum shows it.</Text>
      <Group gap="sm">
        <FileButton accept={ACCEPT} onChange={(chosen) => void choose(chosen)}>
          {(props) => (
            <Button {...props} variant={image ? 'default' : 'filled'}>
              {image ? 'Choose another photo' : 'Choose a photo'}
            </Button>
          )}
        </FileButton>
        <Text size="xs" c="dimmed">{`${picture.formats}, up to ${picture.max_label}.`}</Text>
      </Group>
      {problem ? (
        <Alert color="red" variant="light" role="alert">
          <Text size="sm">{problem}</Text>
        </Alert>
      ) : null}
      {image ? (
        <Stack gap="sm" className={classes.editor}>
          <Cropper image={image} crop={crop} onCrop={setCrop} />
          <Group gap="xs" wrap="nowrap">
            <ActionIcon
              variant="default"
              aria-label="Zoom out"
              onClick={() => {
                zoomBy(-0.1);
              }}
            >
              <IconMinus size={16} />
            </ActionIcon>
            <Slider
              flex={1}
              min={MIN_ZOOM}
              max={MAX_ZOOM}
              step={0.05}
              value={crop.zoom}
              label={null}
              aria-label="Zoom"
              onChange={(zoom) => {
                setCrop(settle(image.width, image.height, { ...crop, zoom }));
              }}
            />
            <ActionIcon
              variant="default"
              aria-label="Zoom in"
              onClick={() => {
                zoomBy(0.1);
              }}
            >
              <IconPlus size={16} />
            </ActionIcon>
          </Group>
          <Group>
            <Button
              loading={send.isPending}
              onClick={() => {
                send.mutate();
              }}
            >
              Send for review
            </Button>
          </Group>
        </Stack>
      ) : null}
    </Stack>
  );
}
