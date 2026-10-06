/**
 * What the settings pages share: the frame of a section (title, crumbs, its
 * loading and error states) and the one way a section is saved -- "Saved."
 * when something changed, "Nothing changed." when not, and a refusal shown at
 * the field it is about.
 */
import { Button, Group } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type ReactNode, useState } from 'react';

import { ApiError } from '../../../api/client';
import { PageHeader } from '../../../components/PageHeader';
import { ErrorState, LoadingState } from '../../../components/States';
import { notifyDone, notifyFailed, notifyNote } from '../../../lib/notify';

export function SettingsPage<T>({
  title,
  description,
  query,
  children,
}: {
  title: string;
  description: string;
  query: { queryKey: readonly unknown[]; queryFn: () => Promise<T> };
  children: (data: T) => ReactNode;
}) {
  const loaded = useQuery(query);
  return (
    <>
      <PageHeader
        title={title}
        description={description}
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Settings' }, { label: title }]}
      />
      {loaded.isPending ? (
        <LoadingState />
      ) : loaded.isError ? (
        <ErrorState error={loaded.error} onRetry={() => void loaded.refetch()} />
      ) : (
        // Keyed by what was loaded, so the form starts afresh from what was saved.
        <div key={JSON.stringify(loaded.data)}>{children(loaded.data)}</div>
      )}
    </>
  );
}

/** Saving a section; ``errors`` holds the server's word per field until that field changes. */
export function useSectionSave<Values, Out extends { changed: string[] }>(
  queryKey: readonly unknown[],
  run: (values: Values) => Promise<Out>,
  done?: (out: Out) => void,
) {
  const client = useQueryClient();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: run,
    onSuccess: async (out) => {
      setErrors({});
      if (out.changed.length) notifyDone('Saved.');
      else notifyNote('Nothing changed.');
      done?.(out);
      await client.invalidateQueries({ queryKey });
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const clear = (field: string) => {
    if (errors[field])
      setErrors((current) => Object.fromEntries(Object.entries(current).filter(([key]) => key !== field)));
  };
  return { save, errors, clear };
}

export function SaveBar({
  busy,
  onSave,
  label = 'Save',
}: {
  busy: boolean;
  onSave: () => void;
  label?: string;
}) {
  return (
    <Group justify="flex-end">
      <Button loading={busy} onClick={onSave}>
        {label}
      </Button>
    </Group>
  );
}

/** Of a secret only whether one is set: typing replaces it, empty keeps it. */
export function secretHint(isSet: boolean): string {
  return isSet ? 'One is set. Leave empty to keep it.' : 'None set yet.';
}
