/**
 * The footer under every page: the Impressum, privacy policy and statutes,
 * all legal texts, contact, the association's website, and the copyright
 * (GET /api/v1/site). Small and quiet, centred; the links wrap on a phone.
 */
import { Anchor, Group, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { api, call } from '../api/client';
import classes from './Frame.module.css';

export const siteQuery = {
  queryKey: ['site'] as const,
  queryFn: () => call(api.GET('/api/v1/site')),
  staleTime: Infinity,
};

export function Footer() {
  const site = useQuery(siteQuery);
  if (!site.data) return null;
  return (
    <footer className={classes.footer}>
      <Group component="ul" justify="center" gap="0.25rem 0.9rem" className={classes.footerLinks}>
        {site.data.footer.map((link) => (
          <li key={link.label}>
            <Anchor
              href={link.url}
              className={classes.footerLink}
              {...(link.external ? { target: '_blank', rel: 'noopener' } : {})}
            >
              {link.label}
              {link.external ? <span aria-hidden="true"> ↗</span> : null}
            </Anchor>
          </li>
        ))}
      </Group>
      <Text className={classes.copyright}>{site.data.copyright}</Text>
    </footer>
  );
}
