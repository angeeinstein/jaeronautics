/**
 * A link to a legal text's PDF, which the server makes the first time it is
 * asked for (blueprints/_legal_pages.py). Followed straight away, it would
 * open an empty tab that fills only once the PDF is made -- or, on a phone,
 * seem to do nothing. So the link asks for it to be made first (?prepare=1),
 * says so meanwhile, and opens it once it is there, at an address carrying
 * the PDF's hash, so a browser holding an earlier copy cannot show that one.
 *
 * A new tab where the browser still allows one so long after the click, this
 * tab otherwise (Safari). A middle or modified click opens the plain link.
 */
import { Anchor, Group, Loader, Text } from '@mantine/core';
import { type MouseEvent, useState } from 'react';

async function prepared(href: string): Promise<string> {
  const address = new URL(href, window.location.origin);
  address.searchParams.set('prepare', '1');
  const response = await fetch(address, {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  });
  const answer = (await response.json().catch(() => ({}))) as { url?: string; error?: string };
  if (!response.ok || !answer.url) throw new Error(answer.error ?? 'The PDF could not be made just now.');
  return answer.url;
}

export function PdfLink({ href, children }: { href: string; children: string }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  function open(event: MouseEvent<HTMLAnchorElement>) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setProblem(null);
    prepared(href)
      .then((url) => {
        // Not "noopener": with it, window.open answers null even when it opened.
        const tab = window.open(url, '_blank');
        if (tab) tab.opener = null;
        else window.location.assign(url);
      })
      .catch((error: unknown) => {
        setProblem(error instanceof Error ? error.message : 'The PDF could not be made just now.');
      })
      .finally(() => {
        setBusy(false);
      });
  }

  return (
    // Inline, so it sits in a line of text ("Version of … · PDF").
    <Group gap={6} wrap="nowrap" component="span" display="inline-flex">
      <Anchor href={href} target="_blank" rel="noopener" onClick={open} aria-busy={busy || undefined}>
        {busy ? (
          <Group gap={6} wrap="nowrap" component="span">
            <Loader size={12} aria-hidden />
            Making the PDF…
          </Group>
        ) : (
          children
        )}
      </Anchor>
      {problem ? (
        <Text span size="sm" c="red" role="alert">
          {problem}
        </Text>
      ) : null}
    </Group>
  );
}
