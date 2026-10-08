/**
 * My Account › Profile: how to reach this person -- both email addresses, the
 * phones, the address -- and their name and kind of membership. Each read
 * first, changed on the spot: contact details at once, the name by a request
 * an admin decides. Data: GET /api/v1/account, the form's choices.
 */
import { Stack } from '@mantine/core';

import { PageHeader } from '../../components/PageHeader';
import { ErrorState, LoadingState } from '../../components/States';
import { NoMembership } from './Account';
import { ContactCard } from './Contact';
import { IdentityCard } from './Identity';
import { useAccount, useFormOptions } from './shared';

export function Profile() {
  const account = useAccount();
  const options = useFormOptions();
  const header = (
    <PageHeader
      title="Profile"
      description="How we reach you, and your name and kind of membership."
      crumbs={[{ label: 'My Account', to: '/account' }, { label: 'Profile' }]}
    />
  );
  if (account.isPending || options.isPending)
    return (
      <>
        {header}
        <LoadingState />
      </>
    );
  if (account.isError || options.isError) {
    const failed = account.isError ? account : options;
    return (
      <>
        {header}
        <ErrorState error={failed.error} onRetry={() => void failed.refetch()} />
      </>
    );
  }
  const member = account.data.member;
  const kind = options.data.member_categories.find(
    (category) => category.value === member?.identity.member_category,
  );
  if (!member)
    return (
      <>
        {header}
        <NoMembership />
      </>
    );
  return (
    <>
      {header}
      <Stack gap="lg">
        <ContactCard
          key={JSON.stringify(member.contact)}
          contact={member.contact}
          options={options.data}
          email={account.data.email}
          workEmail={member.work_email}
          asksCompany={kind?.company_name ?? false}
          labels={{
            account: kind?.account_email === 'private' ? 'Private email' : 'Sign-in email',
            work:
              kind?.work_email_whose === 'student'
                ? 'University email'
                : kind?.work_email_whose === 'staff'
                  ? 'Institute email'
                  : 'University or company email',
          }}
        />
        <IdentityCard
          key={member.change_request?.id ?? 'form'}
          identity={member.identity}
          request={member.change_request}
          options={options.data}
        />
      </Stack>
    </>
  );
}
