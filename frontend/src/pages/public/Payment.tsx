/**
 * Where Stripe's payment page comes back to: /thank-you once it is done (or,
 * for an invoice, right after joining) and /cancel when it was left. What it
 * says depends on how they pay (``?method=invoice``) and whether the year's
 * free months have begun (``?phase=free_period``) -- both set by the server
 * when it made the address (services/billing.py). Teams, when there are any,
 * are pointed to, as a card of their own: what they are called comes from
 * GET /api/v1/site. A square with a check drawn into it opens the page (a
 * dash when the payment was left); nothing moves when the device asks for
 * less motion.
 */
import { Button, Group, Stack, Text, Title } from '@mantine/core';
import { IconUsersGroup } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';

import { AppLink } from '../../app/AppLink';
import { useDocumentTitle } from '../../components/PageHeader';
import { siteQuery } from '../../frame/Footer';
import classes from './Payment.module.css';

function texts(invoice: boolean, free: boolean): [string, string[]] {
  if (invoice) {
    return free
      ? [
          'Thank you for joining!',
          [
            'Your membership is active now and free until 31 December.',
            'The first annual invoice is due on 1 January, unless you cancel before.',
          ],
        ]
      : [
          'Thank you for joining!',
          ['We email you the invoice for this year.', 'Renews every 1 January unless you cancel.'],
        ];
  }
  return free
    ? [
        'Thank you!',
        [
          'Your membership is active now and free until 31 December.',
          'Your payment method is saved. The annual fee is charged on 1 January unless you cancel.',
        ],
      ]
    : [
        'Thank you!',
        [
          'Your membership is set up. It renews every 1 January unless you cancel.',
          'Paying by SEPA Direct Debit? Your membership starts once the debit clears, in a few days. We email you.',
        ],
      ];
}

/** A square with a mark drawn into it: a check, or for the cancelled page a dash. */
function Mark({ kind }: { kind: 'check' | 'dash' }) {
  return (
    <svg
      className={classes.mark}
      data-tone={kind === 'check' ? undefined : 'neutral'}
      viewBox="0 0 72 72"
      aria-hidden="true"
    >
      <rect className={classes.frame} x="1" y="1" width="70" height="70" />
      <path className={classes.stroke} d={kind === 'check' ? 'M22 37 L32 47 L51 27' : 'M24 36 L48 36'} />
    </svg>
  );
}

export function ThankYou() {
  const [params] = useSearchParams();
  const [title, lines] = texts(params.get('method') === 'invoice', params.get('phase') === 'free_period');
  const [lead, ...more] = lines;
  const label = useQuery(siteQuery).data?.teams_label;
  useDocumentTitle('Thank you');
  return (
    <Stack gap="xl" className={classes.page}>
      <Stack gap="sm" className={classes.hero}>
        <Mark kind="check" />
        <Title order={1}>{title}</Title>
        <Text className={classes.lead}>{lead}</Text>
        {more.map((line) => (
          <Text key={line} className={classes.more}>
            {line}
          </Text>
        ))}
        <Group justify="center" mt="md" className={classes.after}>
          <Button component={AppLink} to="/account" size="md">
            Go to My Account
          </Button>
        </Group>
      </Stack>
      {label ? (
        <section className={`${classes.teams} ${classes.after}`} aria-labelledby="thank-you-teams">
          <IconUsersGroup size={28} stroke={1.5} className={classes.teamsIcon} aria-hidden />
          <Stack gap="xs">
            <Title order={2} size="h4" id="thank-you-teams">
              {`Our ${label.toLowerCase()}`}
            </Title>
            <Text size="sm" c="dimmed">
              {`Once your membership is active you can see them, and join or apply. Paying by SEPA Direct Debit, that is once the debit has cleared.`}
            </Text>
            <div>
              <Button component={AppLink} to="/teams" variant="default" mt={4}>
                {`See the ${label.toLowerCase()}`}
              </Button>
            </div>
          </Stack>
        </section>
      ) : null}
    </Stack>
  );
}

export function Cancel() {
  useDocumentTitle('Payment cancelled');
  return (
    <Stack gap="xl" className={classes.page}>
      <Stack gap="sm" className={classes.hero}>
        <Mark kind="dash" />
        <Title order={1}>Payment cancelled</Title>
        <Text className={classes.lead}>You left the payment page. Nothing was charged.</Text>
        <Text className={classes.more}>
          Your account is there: you can finish paying any time from My Account.
        </Text>
        <Group justify="center" mt="md" className={classes.after}>
          <Button component={AppLink} to="/account" size="md">
            Go to My Account
          </Button>
        </Group>
      </Stack>
    </Stack>
  );
}
