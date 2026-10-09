/**
 * My Account › Profile: whether the association's news reaches me by email
 * (docs/messages-plan.md). The same switch as a news email's "No more news by
 * email". Notices to every member and team mailings arrive either way.
 * Data: GET|PUT /api/v1/account/news.
 */
import { Stack, Switch, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api, call } from '../../api/client';
import { Panel } from '../../components/Panel';
import { notifyFailed } from '../../lib/notify';

const newsQuery = {
  queryKey: ['account', 'news'] as const,
  queryFn: () => call(api.GET('/api/v1/account/news')),
};

export function NewsCard() {
  const client = useQueryClient();
  const news = useQuery(newsQuery);
  const change = useMutation({
    mutationFn: (subscribed: boolean) => call(api.PUT('/api/v1/account/news', { body: { subscribed } })),
    onSuccess: (data) => {
      client.setQueryData(newsQuery.queryKey, data);
    },
    onError: notifyFailed,
  });
  if (!news.data) return null;
  return (
    <Panel title="News by email">
      <Stack gap="sm">
        <Switch
          label="The association's news"
          checked={news.data.subscribed}
          disabled={change.isPending}
          onChange={(event) => {
            change.mutate(event.currentTarget.checked);
          }}
        />
        <Text size="sm" c="dimmed">
          Notices to every member, such as the invitation to the general assembly, and your teams&apos; emails
          arrive either way.
        </Text>
      </Stack>
    </Panel>
  );
}
