/**
 * The contact form (docs/messages-plan.md): what it is about, a subject and
 * the message. It goes to the association's admins, who answer by email.
 * Somebody signed in writes as their account -- name and address are shown,
 * not asked -- and the page they came from goes along. Data: GET|POST
 * /api/v1/contact.
 */
import { Alert, Button, Select, SimpleGrid, Stack, Text, Textarea, TextInput, Title } from '@mantine/core';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api, ApiError, call } from '../../api/client';
import { TrapField, useWritingTime } from '../../components/MessageGuard';
import { useDocumentTitle } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { ErrorState, LoadingState } from '../../components/States';
import { notifyFailed } from '../../lib/notify';

const contactQuery = {
  queryKey: ['contact'] as const,
  queryFn: () => call(api.GET('/api/v1/contact')),
};

/** Where they came from, when it was a page of the portal. */
function cameFrom(): string | null {
  try {
    const referrer = new URL(document.referrer);
    return referrer.origin === window.location.origin ? referrer.pathname : null;
  } catch {
    return null;
  }
}

export function Contact() {
  useDocumentTitle('Contact');
  const form = useQuery(contactQuery);
  const writingTime = useWritingTime();
  const [topic, setTopic] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [subject, setSubject] = useState('');
  const [message, setMessage] = useState('');
  const [website, setWebsite] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const send = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/contact', {
          body: {
            topic: topic ?? '',
            name: name || null,
            email: email || null,
            subject,
            message,
            page: cameFrom(),
            website: website || null,
            seconds: writingTime(),
          },
        }),
      ),
    onSuccess: () => {
      setErrors({});
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const clear = (field: string) => {
    if (errors[field]) setErrors({ ...errors, [field]: '' });
  };

  if (form.isPending) return <LoadingState />;
  if (form.isError) return <ErrorState error={form.error} onRetry={() => void form.refetch()} />;
  const signedIn = form.data.signed_in;

  return (
    <Stack gap="lg" maw={640} mx="auto" mt={{ base: 0, sm: 'xl' }}>
      <Title order={1}>Contact</Title>
      <Panel title="Write to us">
        {send.data ? (
          <Stack gap="md">
            <Alert color="green" variant="light" role="status">
              {send.data.message}
            </Alert>
            <Button
              variant="default"
              onClick={() => {
                send.reset();
                setSubject('');
                setMessage('');
              }}
            >
              Write another
            </Button>
          </Stack>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              send.mutate();
            }}
          >
            <Stack gap="md">
              <Text size="sm" c="dimmed">
                {signedIn
                  ? `As ${form.data.name ?? ''} (${form.data.email ?? ''}). We answer by email.`
                  : 'We answer by email.'}
              </Text>
              {signedIn ? null : (
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <TextInput
                    label="Name"
                    autoComplete="name"
                    required
                    maxLength={120}
                    value={name}
                    error={errors.name}
                    onChange={(event) => {
                      setName(event.currentTarget.value);
                      clear('name');
                    }}
                  />
                  <TextInput
                    label="Email"
                    type="email"
                    autoComplete="email"
                    required
                    maxLength={255}
                    value={email}
                    error={errors.email}
                    onChange={(event) => {
                      setEmail(event.currentTarget.value);
                      clear('email');
                    }}
                  />
                </SimpleGrid>
              )}
              <Select
                label="About"
                placeholder="Choose"
                required
                data={form.data.topics.map((item) => ({ value: item.value, label: item.label }))}
                value={topic}
                error={errors.topic}
                onChange={(value) => {
                  setTopic(value);
                  clear('topic');
                }}
                allowDeselect={false}
              />
              <TextInput
                label="Subject"
                required
                maxLength={150}
                value={subject}
                error={errors.subject}
                onChange={(event) => {
                  setSubject(event.currentTarget.value);
                  clear('subject');
                }}
              />
              <Textarea
                label="Message"
                required
                autosize
                minRows={6}
                maxLength={5000}
                value={message}
                error={errors.message}
                onChange={(event) => {
                  setMessage(event.currentTarget.value);
                  clear('message');
                }}
              />
              <TrapField value={website} onChange={setWebsite} />
              <Button type="submit" loading={send.isPending}>
                Send
              </Button>
            </Stack>
          </form>
        )}
      </Panel>
    </Stack>
  );
}
