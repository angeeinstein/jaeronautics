/**
 * The top of every page (docs/frontend-structure.md, 7): the breadcrumb --
 * the area first, the current page plain -- then the title, a one-line
 * description and the page's actions on the right, under the title on a phone.
 */
import { Anchor, Breadcrumbs as MantineBreadcrumbs, Group, Stack, Text, Title } from '@mantine/core';
import { type ReactNode, useEffect } from 'react';

import { AppLink } from '../app/AppLink';

export interface Crumb {
  label: string;
  /** Left out for the current page. */
  to?: string;
}

export function Breadcrumbs({ crumbs }: { crumbs: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb">
      <MantineBreadcrumbs separator="›" separatorMargin={6} fz="sm">
        {crumbs.map((crumb) =>
          crumb.to ? (
            <Anchor key={crumb.label} component={AppLink} to={crumb.to} size="sm">
              {crumb.label}
            </Anchor>
          ) : (
            <Text key={crumb.label} size="sm" c="dimmed" aria-current="page">
              {crumb.label}
            </Text>
          ),
        )}
      </MantineBreadcrumbs>
    </nav>
  );
}

interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  crumbs?: Crumb[];
  actions?: ReactNode;
}

export function PageHeader({ title, description, crumbs, actions }: PageHeaderProps) {
  useEffect(() => {
    document.title = `${title} · Joanneum Aeronautics`;
  }, [title]);

  return (
    <Stack gap="xs" mb="lg">
      {crumbs?.length ? <Breadcrumbs crumbs={crumbs} /> : null}
      <Group justify="space-between" align="flex-end" gap="md">
        <Stack gap={4}>
          <Title order={1}>{title}</Title>
          {description ? <Text c="dimmed">{description}</Text> : null}
        </Stack>
        {actions ? <Group gap="sm">{actions}</Group> : null}
      </Group>
    </Stack>
  );
}
