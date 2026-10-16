/**
 * Under an email field: "Did you mean anna@edu.fh-joanneum.at?" when the
 * address typed looks like a mistyped university address (lib/emailHint.ts),
 * the likely one a click away. A hint only: the address can be used as typed.
 * The domains are the portal's own (GET /api/v1/forms/options).
 */
import { Anchor, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { likelyMeant } from '../lib/emailHint';
import { formOptionsQuery } from '../pages/account/shared';

export function EmailHint({ value, onUse }: { value: string; onUse: (meant: string) => void }) {
  const options = useQuery(formOptionsQuery);
  const domains = options.data ? [...options.data.student_domains, ...options.data.staff_domains] : [];
  const meant = likelyMeant(value, domains);
  if (!meant) return null;
  return (
    <Text size="sm" c="amber.4" mt={4} role="status">
      Did you mean{' '}
      <Anchor
        component="button"
        type="button"
        size="sm"
        onClick={() => {
          onUse(meant);
        }}
      >
        {meant}
      </Anchor>
      ?
    </Text>
  );
}
