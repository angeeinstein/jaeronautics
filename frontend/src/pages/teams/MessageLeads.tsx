/**
 * "Message the leads" on a team's pages (docs/messages-plan.md): a subject
 * and a message to the team's leads -- to the admins while it has none. The
 * leads answer by email; their addresses stay hidden until they do.
 * Data: POST /api/v1/teams/<slug>/message.
 */
import { Alert, Button, Modal, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { IconMail } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { api, ApiError, call } from '../../api/client';
import { TrapField, useWritingTime } from '../../components/MessageGuard';
import { notifyFailed } from '../../lib/notify';

function Form({ slug, onDone }: { slug: string; onDone: () => void }) {
  const writingTime = useWritingTime();
  const [subject, setSubject] = useState('');
  const [message, setMessage] = useState('');
  const [website, setWebsite] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const send = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/teams/{slug}/message', {
          params: { path: { slug } },
          body: { subject, message, website: website || null, seconds: writingTime() },
        }),
      ),
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  if (send.data) {
    return (
      <Stack gap="md">
        <Alert color="green" variant="light" role="status">
          {send.data.message}
        </Alert>
        <Button variant="default" onClick={onDone}>
          Close
        </Button>
      </Stack>
    );
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        send.mutate();
      }}
    >
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          They answer by email, to the address of your account.
        </Text>
        <TextInput
          label="Subject"
          required
          maxLength={150}
          value={subject}
          error={errors.subject}
          onChange={(event) => {
            setSubject(event.currentTarget.value);
          }}
          data-autofocus
        />
        <Textarea
          label="Message"
          required
          autosize
          minRows={5}
          maxLength={5000}
          value={message}
          error={errors.message}
          onChange={(event) => {
            setMessage(event.currentTarget.value);
          }}
        />
        <TrapField value={website} onChange={setWebsite} />
        <Button type="submit" loading={send.isPending}>
          Send
        </Button>
      </Stack>
    </form>
  );
}

export function MessageLeads({ slug, teamName }: { slug: string; teamName: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="default"
        leftSection={<IconMail size={16} aria-hidden />}
        onClick={() => {
          setOpen(true);
        }}
      >
        Message the leads
      </Button>
      <Modal
        opened={open}
        onClose={() => {
          setOpen(false);
        }}
        title={`Message the leads of ${teamName}`}
        size="lg"
      >
        {open ? (
          <Form
            slug={slug}
            onDone={() => {
              setOpen(false);
            }}
          />
        ) : null}
      </Modal>
    </>
  );
}
