/**
 * Where Stripe's payment page comes back to: /thank-you once it is done (or,
 * for an invoice, right after joining) and /cancel when it was left. What it
 * says depends on how they pay (``?method=invoice``) and whether the year's
 * free months have begun (``?phase=free_period``) -- both set by the server
 * when it made the address (services/billing.py). Teams, when there are any,
 * are pointed to: what they are called comes from GET /api/v1/site.
 */
import { Button, Divider, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';

import { AppLink } from '../../app/AppLink';
import { useDocumentTitle } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { siteQuery } from '../../frame/Footer';

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

export function ThankYou() {
  const [params] = useSearchParams();
  const [title, lines] = texts(params.get('method') === 'invoice', params.get('phase') === 'free_period');
  const label = useQuery(siteQuery).data?.teams_label;
  useDocumentTitle('Thank you');
  return (
    <Stack gap="lg" maw={640} mx="auto" mt={{ base: 0, sm: 'xl' }}>
      <Title order={1}>{title}</Title>
      <Panel title="Your membership">
        <Stack gap="sm">
          {lines.map((line) => (
            <Text key={line}>{line}</Text>
          ))}
          <div>
            <Button component={AppLink} to="/account" mt="xs">
              Go to My Account
            </Button>
          </div>
          {label ? (
            <>
              <Divider my="xs" />
              <Text size="sm">
                {`Interested in one of our ${label.toLowerCase()}? Once your membership is active, you can see them and join or apply. Paying by SEPA Direct Debit, that is once the debit has cleared.`}
              </Text>
              <div>
                <Button component={AppLink} to="/teams" variant="default" size="sm">
                  {`See the ${label.toLowerCase()}`}
                </Button>
              </div>
            </>
          ) : null}
        </Stack>
      </Panel>
    </Stack>
  );
}

export function Cancel() {
  useDocumentTitle('Payment cancelled');
  return (
    <Stack gap="lg" maw={640} mx="auto" mt={{ base: 0, sm: 'xl' }}>
      <Title order={1}>Payment cancelled</Title>
      <Panel title="Nothing was charged">
        <Stack gap="sm">
          <Text>You left the payment page. You have not been charged.</Text>
          <Text>Your account is there: you can finish paying any time from My Account.</Text>
          <div>
            <Button component={AppLink} to="/account" mt="xs">
              Go to My Account
            </Button>
          </div>
        </Stack>
      </Panel>
    </Stack>
  );
}
