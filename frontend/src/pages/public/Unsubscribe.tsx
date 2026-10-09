/**
 * Where a news email's "No more news by email" leads (docs/messages-plan.md):
 * the address it was for, and one button -- nothing changes on merely
 * opening the page, since mail scanners open links. Afterwards the way back,
 * "Subscribe again". Notices, such as the general assembly's invitation, and
 * team mailings still arrive. Data: GET|PUT /api/v1/news/<token>.
 */
import { Alert, Button, Stack, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router';

import { api, call } from '../../api/client';
import { useDocumentTitle } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { ErrorState, LoadingState } from '../../components/States';
import { notifyFailed } from '../../lib/notify';

export function Unsubscribe() {
  useDocumentTitle('News by email');
  const { token = '' } = useParams();
  const client = useQueryClient();
  const key = ['news', token] as const;
  const news = useQuery({
    queryKey: key,
    queryFn: () => call(api.GET('/api/v1/news/{token}', { params: { path: { token } } })),
    retry: false,
  });
  const change = useMutation({
    mutationFn: (subscribed: boolean) =>
      call(api.PUT('/api/v1/news/{token}', { params: { path: { token } }, body: { subscribed } })),
    onSuccess: (data) => {
      client.setQueryData(key, data);
    },
    onError: notifyFailed,
  });

  return (
    <Stack gap="lg" maw={520} mx="auto" mt={{ base: 0, sm: 'xl' }}>
      <Title order={1}>News by email</Title>
      {news.isPending ? (
        <LoadingState />
      ) : news.isError ? (
        <ErrorState error={news.error} />
      ) : (
        <Panel title={news.data.email}>
          <Stack gap="md">
            {news.data.subscribed ? (
              <>
                <Text size="sm">The association&apos;s news reaches this address by email.</Text>
                <Button
                  loading={change.isPending}
                  onClick={() => {
                    change.mutate(false);
                  }}
                >
                  No more news by email
                </Button>
              </>
            ) : (
              <>
                <Alert color="green" variant="light" role="status">
                  No more news by email.
                </Alert>
                <Button
                  variant="default"
                  loading={change.isPending}
                  onClick={() => {
                    change.mutate(true);
                  }}
                >
                  Subscribe again
                </Button>
              </>
            )}
            <Text size="sm" c="dimmed">
              Notices to every member, such as the invitation to the general assembly, and your teams&apos;
              emails still arrive. The setting is also in My Account.
            </Text>
          </Stack>
        </Panel>
      )}
    </Stack>
  );
}
