/**
 * The German Markdown file of a version of a legal text, chosen to upload --
 * its English is made by machine once it is published
 * (docs/legal-translation-plan.md) -- and what was wrong with it when the
 * server refused it (``details.problems``). Used for a preview, a draft, a
 * correction (Admin › Legal texts), a team's rules (its leads' Rules page)
 * and *Replace with a file* in the editor.
 */
import { Alert, FileInput, List } from '@mantine/core';

import { ApiError } from '../../api/client';

export interface ChosenFiles {
  german: File | null;
}

export const NO_FILES: ChosenFiles = { german: null };

/** The files as the upload endpoints take them. */
export function formOf(files: ChosenFiles): { german: string } {
  const form = new FormData();
  if (files.german) form.append('german', files.german);
  // openapi-fetch sends a FormData as it is, with its own Content-Type.
  return form as unknown as { german: string };
}

/** What was wrong with the files, when that is why the server refused them. */
export function problemsOf(error: unknown): string[] | null {
  const listed = error instanceof ApiError ? error.details.problems : undefined;
  return Array.isArray(listed) ? listed.map(String) : null;
}

export function Problems({ problems, title }: { problems: string[]; title: string }) {
  if (!problems.length) return null;
  return (
    <Alert color="amber" variant="light" title={title}>
      <List size="sm" spacing={2}>
        {problems.map((problem) => (
          <List.Item key={problem}>{problem}</List.Item>
        ))}
      </List>
    </Alert>
  );
}

export function LegalFiles({
  value,
  onChange,
}: {
  value: ChosenFiles;
  onChange: (files: ChosenFiles) => void;
}) {
  return (
    <FileInput
      label="German file"
      accept=".md,text/markdown,text/plain"
      placeholder="Choose a .md file"
      clearable
      maw={480}
      value={value.german}
      onChange={(file) => {
        onChange({ german: file });
      }}
    />
  );
}
