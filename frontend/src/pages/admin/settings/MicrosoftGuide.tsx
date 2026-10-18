/**
 * The Microsoft 365 setup guide, in a window over Settings › Email ›
 * Accounts: docs/microsoft-mail-setup.md as the server renders it, with the
 * tenant, the app and the mailboxes' domain it knows put in for the
 * placeholders. Each command block has a *Copy* button.
 * Data: GET /api/v1/admin/settings/mail/microsoft/guide.
 */
import { Button, Modal } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { IconBook } from '@tabler/icons-react';

import { api, call } from '../../../api/client';
import { ErrorState, LoadingState } from '../../../components/States';
import classes from './MicrosoftGuide.module.css';

function Guide({ html }: { html: string }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const blocks = box.current?.querySelectorAll('pre') ?? [];
    const buttons: HTMLButtonElement[] = [];
    for (const block of blocks) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = classes.copy ?? '';
      button.textContent = 'Copy';
      button.addEventListener('click', () => {
        void navigator.clipboard.writeText(block.querySelector('code')?.textContent ?? '').then(() => {
          button.textContent = 'Copied';
          window.setTimeout(() => {
            button.textContent = 'Copy';
          }, 1500);
        });
      });
      block.append(button);
      buttons.push(button);
    }
    return () => {
      for (const button of buttons) button.remove();
    };
  }, [html]);
  return <div ref={box} className={classes.guide} dangerouslySetInnerHTML={{ __html: html }} />;
}

export function MicrosoftGuide() {
  const [opened, setOpened] = useState(false);
  const phone = useMediaQuery('(max-width: 48em)');
  const guide = useQuery({
    queryKey: ['admin', 'settings', 'mail', 'microsoft', 'guide'],
    queryFn: () => call(api.GET('/api/v1/admin/settings/mail/microsoft/guide')),
    enabled: opened,
  });
  return (
    <>
      <Button
        variant="default"
        leftSection={<IconBook size={16} />}
        onClick={() => {
          setOpened(true);
        }}
      >
        Setup guide
      </Button>
      <Modal
        opened={opened}
        onClose={() => {
          setOpened(false);
        }}
        title="Setting up Microsoft 365"
        size="xl"
        fullScreen={phone}
      >
        {guide.data ? (
          <Guide html={guide.data.html} />
        ) : guide.isError ? (
          <ErrorState error={guide.error} onRetry={() => void guide.refetch()} />
        ) : (
          <LoadingState />
        )}
      </Modal>
    </>
  );
}
