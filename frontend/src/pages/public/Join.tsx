/**
 * Joining (/join): the login and the membership together, step by step, then
 * on to paying (POST /api/v1/signup). The form is MembershipForm, shared with a
 * membership for somebody signed in without one (pages/account/CreateMembership.tsx).
 */
import { Anchor } from '@mantine/core';

import { AppLink } from '../../app/AppLink';
import { PageHeader } from '../../components/PageHeader';
import { MembershipForm } from './MembershipForm';

export function Join() {
  return (
    <>
      <PageHeader
        title="Become a member"
        description={
          <>
            A few short steps: your account here and in the forum is made from them. Already have one?{' '}
            <Anchor component={AppLink} to="/login">
              Sign in
            </Anchor>
          </>
        }
      />
      <MembershipForm door={{ kind: 'signup' }} />
    </>
  );
}
