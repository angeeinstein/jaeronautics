/**
 * The Markdown files of a version of a legal text -- the German one and,
 * if there is one, its English translation -- chosen to upload, and what was
 * wrong with them when the server refused them (``details.problems``). Used
 * for a preview, a draft, a correction (Admin › Legal texts) and a team's
 * rules (its leads' Rules page).
 */
import { Alert, FileInput, List, SimpleGrid } from '@mantine/core';

import { ApiError } from '../../api/client';

export interface ChosenFiles {
  german: File | null;
  english: File | null;
}

export const NO_FILES: ChosenFiles = { german: null, english: null };

/** The files as the upload endpoints take them. */
export function formOf(files: ChosenFiles): { german: string } {
  const form = new FormData();
  if (files.german) form.append('german', files.german);
  if (files.english) form.append('english', files.english);
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
    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
      <FileInput
        label="German file"
        accept=".md,text/markdown,text/plain"
        placeholder="Choose a .md file"
        clearable
        value={value.german}
        onChange={(file) => {
          onChange({ ...value, german: file });
        }}
      />
      <FileInput
        label="English translation (optional)"
        accept=".md,text/markdown,text/plain"
        placeholder="Choose a .md file"
        clearable
        value={value.english}
        onChange={(file) => {
          onChange({ ...value, english: file });
        }}
      />
    </SimpleGrid>
  );
}
