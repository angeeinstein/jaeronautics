/**
 * The page the deletion email links to: exactly what deleting does to this
 * account, then the button that does it. Opening it changes nothing --
 * mail clients and link scanners open links without being asked. While a paid
 * membership runs it says what is lost, and that cancelling would keep it.
 * Data: GET|POST /api/v1/account/deletion/<token>.
 */
import { Alert, Button, Group, List, Stack, Text } from '@mantine/core';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router';

import { api, call, type Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { ConfirmButton } from '../../components/ConfirmButton';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { ErrorState, LoadingState } from '../../components/States';
import { formatDate, formatEuros } from '../../lib/format';
import { notifyFailed } from '../../lib/notify';
import { goTo } from './shared';

function What({ token, impact }: { token: string; impact: Schemas['DeletionOut'] }) {
  const erase = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/account/deletion/{token}', {
          params: { path: { token } },
          body: { confirm: true },
        }),
      ),
    onSuccess: () => {
      // Signed out now; the start page says it is done.
      goTo('/');
    },
    onError: notifyFailed,
  });
  return (
    <Panel title="What happens">
      <Stack gap="md">
        <Text size="sm">This erases your personal data and cannot be undone.</Text>
        <List size="sm" spacing="xs">
          <List.Item>Your name, address, phone numbers and emails are erased.</List.Item>
          <List.Item>Your login stops working at once, and you are signed out.</List.Item>
          {impact.has_forum_account ? (
            <List.Item>
              Your forum account is anonymised. Your posts stay in their threads under an anonymous name, so
              other members’ conversations are not broken up.
            </List.Item>
          ) : null}
          <List.Item>
            We keep a record of which membership periods were paid for, and the matching invoice numbers,
            without your personal details. Austrian bookkeeping law requires us to keep this for seven years.
          </List.Item>
          {impact.has_stripe_customer ? (
            <List.Item>
              Your payments were handled by Stripe, who keep their own record of them — including the name and
              address on each invoice — under their own rules and Austrian bookkeeping law. Deleting your
              account here does not delete that. Your data download names your Stripe customer number, so you
              can ask Stripe directly.
            </List.Item>
          ) : null}
        </List>
        {impact.subscription_active ? (
          <Alert color="amber" variant="light">
            <Text size="sm" fw={600}>
              Your membership will be cancelled at once, without a refund.
            </Text>
            <Text size="sm">
              {impact.paid_until
                ? `You have paid until ${formatDate(impact.paid_until)}, and that time is lost. `
                : ''}
              If you only want to stop being a member, cancel your membership instead: it runs until the end
              of what you paid for, and you can still delete your account afterwards.
            </Text>
          </Alert>
        ) : impact.paid_until ? (
          <Alert color="amber" variant="light">
            <Text size="sm">
              {`Your membership is paid until ${formatDate(impact.paid_until)}. Deleting your account ends it now, and the rest is not refunded.`}
            </Text>
          </Alert>
        ) : null}
        {impact.credit_cents > 0 ? (
          <Alert color="brand" variant="light">
            <Text size="sm">
              {`Your credit of ${formatEuros(impact.credit_cents)} is refunded to the card or account you paid it with. Credit handed over in cash is paid out by the treasurer.`}
            </Text>
          </Alert>
        ) : null}
        {impact.is_last_admin ? (
          <Alert color="red" variant="light">
            <Text size="sm">
              You are the only administrator. Give someone else admin access first, or nobody can run the
              portal.
            </Text>
          </Alert>
        ) : null}
        <Group gap="sm">
          <Button component="a" href={impact.export_url} variant="default">
            Download my data first
          </Button>
          <Button component={AppLink} to="/account">
            Keep my account
          </Button>
          {impact.is_last_admin ? null : (
            <ConfirmButton
              color="red"
              variant="outline"
              confirmLabel="Yes, delete it"
              loading={erase.isPending}
              onConfirm={() => {
                erase.mutate();
              }}
            >
              Delete my account
            </ConfirmButton>
          )}
        </Group>
      </Stack>
    </Panel>
  );
}

export function DeleteAccount() {
  const { token = '' } = useParams();
  const impact = useQuery({
    queryKey: ['account', 'deletion', token] as const,
    queryFn: () => call(api.GET('/api/v1/account/deletion/{token}', { params: { path: { token } } })),
    retry: false,
  });
  return (
    <>
      <PageHeader
        title="Delete your account"
        crumbs={[{ label: 'My Account', to: '/account' }, { label: 'Delete' }]}
      />
      {impact.isPending ? (
        <LoadingState />
      ) : impact.isError ? (
        <ErrorState error={impact.error} />
      ) : (
        <What token={token} impact={impact.data} />
      )}
    </>
  );
}
