/**
 * A team's rules: on a page of their own (/teams/<slug>/rules[/<language>
 * [/<version>]], linked from emails), in a dialog over the join form, and as
 * one line on the team's pages -- which version applies, which one somebody
 * accepted. Data: GET /api/v1/teams/<slug>/rules.
 */
import { Anchor, Box, Modal, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router';

import { api, call, type Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { LegalTextBody, LegalTextPage } from '../../components/legal/LegalText';
import { Breadcrumbs, useDocumentTitle } from '../../components/PageHeader';
import { PdfLink } from '../../components/PdfLink';
import { ErrorState, LoadingState } from '../../components/States';
import { formatDate } from '../../lib/format';
import { useTeam } from './shared';

type Language = 'de' | 'en';

function useRules(slug: string, language?: Language, version?: string) {
  return useQuery({
    queryKey: ['teams', slug, 'rules', language ?? null, version ?? null] as const,
    queryFn: () =>
      call(
        api.GET('/api/v1/teams/{slug}/rules', {
          params: {
            path: { slug },
            query: { ...(language ? { language } : {}), ...(version ? { version } : {}) },
          },
        }),
      ),
  });
}

export function Rules() {
  const { slug = '', language, version } = useParams();
  const team = useTeam(slug);
  const rules = useRules(slug, language === 'de' || language === 'en' ? language : undefined, version);

  useDocumentTitle(rules.data?.title ?? 'Rules');
  if (rules.isPending) return <LoadingState />;
  if (rules.isError) return <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />;
  return (
    <>
      <Breadcrumbs
        crumbs={[
          { label: team.data?.labels.plural ?? 'Teams', to: '/teams' },
          { label: team.data?.name ?? slug, to: `/teams/${slug}/about` },
          { label: 'Rules' },
        ]}
      />
      <Box mt="lg">
        <LegalTextPage text={rules.data} />
      </Box>
    </>
  );
}

/** The rules in a dialog, so reading them does not leave the form. */
export function RulesDialog({
  slug,
  opened,
  onClose,
}: {
  slug: string;
  opened: boolean;
  onClose: () => void;
}) {
  const rules = useQuery({ ...rulesQuery(slug), enabled: opened });
  return (
    <Modal opened={opened} onClose={onClose} size="xl" title="Rules" centered>
      {rules.isPending ? (
        <LoadingState />
      ) : rules.isError ? (
        <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />
      ) : (
        <LegalTextBody text={rules.data} />
      )}
    </Modal>
  );
}

function rulesQuery(slug: string) {
  return {
    queryKey: ['teams', slug, 'rules', null, null] as const,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/rules', { params: { path: { slug } } })),
  };
}

/** Which version applies, which one this person accepted -- and where to read it. */
export function RulesLine({ slug, name, rules }: { slug: string; name: string; rules: Schemas['RulesOut'] }) {
  return (
    <Text size="sm">
      <Anchor component={AppLink} to={`/teams/${slug}/rules`} size="sm">
        {`Rules of ${name}`}
      </Anchor>
      {` (version of ${formatDate(rules.version)}) · `}
      <PdfLink href={`/teams/${slug}/rules/pdf`}>PDF</PdfLink>
      {rules.accepted ? ` · You accepted the version of ${formatDate(rules.accepted)}.` : ''}
      {rules.changed_since ? (
        <Text span size="sm" c="var(--ja-warning)" display="block">
          The rules have changed since; the version above applies now.
        </Text>
      ) : null}
    </Text>
  );
}
