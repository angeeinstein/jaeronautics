/**
 * A team's photos on its About page: a grid, the first one large where the
 * rest fill the rows around it, each opening big in a viewer -- with the
 * caption, and the next and previous with the buttons or the arrow keys.
 * The photos are the team's, chosen by its leads (Team page settings).
 */
import { ActionIcon, Group, Modal, Text } from '@mantine/core';
import { IconChevronLeft, IconChevronRight } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import type { Schemas } from '../../api/client';
import classes from './About.module.css';

type Photo = Schemas['PhotoOut'];

/** One large and the rest in full rows of three: 3, 6, 9 photos. Else all the same size. */
function featured(count: number) {
  return count >= 3 && count % 3 === 0;
}

function Viewer({
  photos,
  shown,
  onShow,
  onClose,
  teamName,
}: {
  photos: Photo[];
  shown: number | null;
  onShow: (index: number) => void;
  onClose: () => void;
  teamName: string;
}) {
  const photo = shown === null ? undefined : photos[shown];
  const index = shown ?? 0;
  const count = photos.length;
  useEffect(() => {
    if (shown === null) return undefined;
    const step = (event: KeyboardEvent) => {
      if (event.key === 'ArrowRight') onShow((shown + 1) % count);
      if (event.key === 'ArrowLeft') onShow((shown - 1 + count) % count);
    };
    window.addEventListener('keydown', step);
    return () => {
      window.removeEventListener('keydown', step);
    };
  }, [shown, count, onShow]);
  return (
    <Modal
      opened={photo !== undefined}
      onClose={onClose}
      size="min(72rem, 100%)"
      centered
      title={shown !== null && count > 1 ? `${String(shown + 1)} / ${String(count)}` : teamName}
      classNames={{ body: classes.viewerBody }}
    >
      {photo ? (
        <figure className={classes.viewer}>
          <img
            className={classes.viewerImage}
            src={photo.url}
            alt={photo.caption ?? `Photo of ${teamName}`}
            width={photo.width}
            height={photo.height}
          />
          <Group justify="space-between" align="center" gap="sm" mt="sm" wrap="nowrap">
            {count > 1 ? (
              <ActionIcon
                variant="default"
                size="lg"
                aria-label="Previous photo"
                onClick={() => {
                  onShow((index - 1 + count) % count);
                }}
              >
                <IconChevronLeft size={20} />
              </ActionIcon>
            ) : (
              <span />
            )}
            <Text component="figcaption" size="sm" ta="center" c={photo.caption ? undefined : 'dimmed'}>
              {photo.caption ?? ''}
            </Text>
            {count > 1 ? (
              <ActionIcon
                variant="default"
                size="lg"
                aria-label="Next photo"
                onClick={() => {
                  onShow((index + 1) % count);
                }}
              >
                <IconChevronRight size={20} />
              </ActionIcon>
            ) : (
              <span />
            )}
          </Group>
        </figure>
      ) : null}
    </Modal>
  );
}

export function Gallery({ photos, teamName }: { photos: Photo[]; teamName: string }) {
  const [shown, setShown] = useState<number | null>(null);
  if (!photos.length) return null;
  return (
    <section aria-label="Photos">
      <ul className={classes.gallery} data-featured={featured(photos.length) || undefined}>
        {photos.map((photo, index) => (
          <li key={photo.id}>
            <button
              type="button"
              className={classes.tile}
              aria-label={photo.caption ? `Open the photo: ${photo.caption}` : 'Open the photo'}
              onClick={() => {
                setShown(index);
              }}
            >
              <img
                src={photo.url}
                alt=""
                loading={index < 3 ? 'eager' : 'lazy'}
                width={photo.width}
                height={photo.height}
              />
              {photo.caption ? <span className={classes.caption}>{photo.caption}</span> : null}
            </button>
          </li>
        ))}
      </ul>
      <Viewer
        photos={photos}
        shown={shown}
        onShow={setShown}
        onClose={() => {
          setShown(null);
        }}
        teamName={teamName}
      />
    </section>
  );
}
