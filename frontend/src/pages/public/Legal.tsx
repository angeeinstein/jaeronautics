/**
 * The association's legal texts: the list (/legal, linked from the footer)
 * and one text (/legal/<slug>[/<language>[/<version>]]) -- the version in
 * force or an earlier one, German or the English translation, with its
 * contents and PDF. Data: GET /api/v1/legal, GET /api/v1/legal/<slug>.
 */
import { Anchor, Box, Group, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useParams } from 'react-router';

import { api, call } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { languageOf, LegalTextPage } from '../../components/legal/LegalText';
import { Breadcrumbs, PageHeader, useDocumentTitle } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { PdfLink } from '../../components/PdfLink';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import { formatDate } from '../../lib/format';

export function LegalTexts() {
  const texts = useQuery({ queryKey: ['legal'] as const, queryFn: () => call(api.GET('/api/v1/legal')) });
  return (
    <>
      <PageHeader
        title="Legal texts"
        description="The texts that apply to membership and this portal, each in the version in force. The German texts apply; the English translations are for convenience."
      />
      {texts.isPending ? (
        <LoadingState />
      ) : texts.isError ? (
        <ErrorState error={texts.error} onRetry={() => void texts.refetch()} />
      ) : (
        <Panel title="In force" flush>
          {texts.data.texts.length ? (
            <Stack gap={0}>
              {texts.data.texts.map((text) => (
                <Group
                  key={text.slug}
                  justify="space-between"
                  gap="xs"
                  px="md"
                  py="sm"
                  style={{ borderTop: '1px solid var(--ja-border)' }}
                >
                  <div>
                    <Anchor component={AppLink} to={text.url} fw={600}>
                      {text.name}
                    </Anchor>
                    <Text size="sm" c="dimmed" lang="de">
                      {text.title}
                    </Text>
                  </div>
                  <Group gap="sm">
                    <Text size="sm" c="dimmed">{`In force since ${formatDate(text.in_force_since)}`}</Text>
                    <PdfLink href={text.pdf_url}>PDF</PdfLink>
                  </Group>
                </Group>
              ))}
            </Stack>
          ) : (
            <Box px="md">
              <EmptyState>None yet.</EmptyState>
            </Box>
          )}
        </Panel>
      )}
    </>
  );
}

export function LegalText() {
  const { slug = '', version } = useParams();
  const language = languageOf(useLocation().pathname);
  const text = useQuery({
    queryKey: ['legal', slug, language ?? null, version ?? null] as const,
    queryFn: () =>
      call(
        api.GET('/api/v1/legal/{slug}', {
          params: {
            path: { slug },
            query: {
              ...(language ? { language } : {}),
              ...(version ? { version } : {}),
            },
          },
        }),
      ),
    retry: false,
  });
  useDocumentTitle(text.data?.title ?? 'Legal text');
  return (
    <>
      <Breadcrumbs crumbs={[{ label: 'Legal texts', to: '/legal' }, { label: text.data?.title ?? '' }]} />
      <Box mt="lg">
        {text.isPending ? (
          <LoadingState />
        ) : text.isError ? (
          <ErrorState error={text.error} />
        ) : (
          <LegalTextPage text={text.data} />
        )}
      </Box>
    </>
  );
}
