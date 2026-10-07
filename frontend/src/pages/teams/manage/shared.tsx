/**
 * What a team's management pages share: the team (what this person may do in
 * it, how many applications wait), the top of each page, and the one way a
 * change is sent -- its result kept, "Saved." said, a refusal shown where it
 * belongs. Data: aeronautics_members/api/team_manage.py.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useParams } from 'react-router';

import { api, ApiError, call, type Schemas } from '../../../api/client';
import { type Crumb, PageHeader } from '../../../components/PageHeader';
import { notifyDone, notifyFailed } from '../../../lib/notify';

export type Manage = Schemas['ManageOut'];
export type TeamPermission = Manage['permissions'][number];

export const manageKey = (slug: string) => ['teams', slug, 'manage'] as const;

export function manageQuery(slug: string) {
  return {
    queryKey: manageKey(slug),
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage', { params: { path: { slug } } })),
  };
}

export function useSlug() {
  return useParams().slug ?? '';
}

/** The team, as the layout already loaded it. */
export function useManage() {
  return useQuery(manageQuery(useSlug()));
}

export function may(manage: Manage | undefined, permission: TeamPermission) {
  return manage?.permissions.includes(permission) ?? false;
}

/** The top of a management page: Teams › Rocket Team › Members. */
export function ManageHeader({
  title,
  description,
  crumbs = [],
  actions,
}: {
  title: string;
  description?: string;
  /** Between the team and this page: a person's page sits below Members. */
  crumbs?: Crumb[];
  actions?: React.ReactNode;
}) {
  const manage = useManage().data;
  const slug = useSlug();
  return (
    <PageHeader
      title={title}
      description={description}
      actions={actions}
      crumbs={[
        { label: manage?.labels.plural ?? 'Teams', to: '/teams' },
        { label: manage?.name ?? slug, to: `/teams/${slug}/manage` },
        ...crumbs,
        { label: title },
      ]}
    />
  );
}

/**
 * A change in the management: on success the answer replaces what the page
 * shows (``key``), the team's counts are fetched again and ``done`` is said;
 * a refusal about one field goes into ``errors``, anything else is notified.
 */
export function useManageChange<In, Out>(
  key: readonly unknown[],
  send: (input: In) => Promise<Out>,
  done: string | ((out: Out) => string),
) {
  const client = useQueryClient();
  const slug = useSlug();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const mutation = useMutation({
    mutationFn: send,
    onSuccess: async (out) => {
      setErrors({});
      client.setQueryData(key, out);
      notifyDone(typeof done === 'string' ? done : done(out));
      await client.invalidateQueries({ queryKey: manageKey(slug), exact: true });
      await client.invalidateQueries({ queryKey: ['teams'], exact: false, refetchType: 'none' });
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  return { mutation, errors, setErrors };
}
