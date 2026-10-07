/**
 * A legal text -- one of the association's, or a team's rules -- as the
 * server answers it (aeronautics_members/api/legal.py): a translation says
 * first that the German text applies; then the title, which version this is,
 * the other language and the PDF; then the text. ``LegalTextPage`` adds the
 * contents and the other versions beside it; the text alone goes into a
 * dialog over a form.
 */
import { Alert, Anchor, List, Stack, Text, Title } from '@mantine/core';

import type { Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { formatDate } from '../../lib/format';
import { Panel } from '../Panel';
import { PdfLink } from '../PdfLink';
import classes from './LegalText.module.css';

export type LegalTextData = Schemas['LegalTextOut'];

function Facts({ text }: { text: LegalTextData }) {
  return (
    <Text size="sm" c="dimmed" lang="en">
      {`Version of ${formatDate(text.version)}`}
      {text.revision ? ` (${text.revision})` : ''}
      {text.effective_from !== text.version ? `, in force from ${formatDate(text.effective_from)}` : ''}
      {text.version !== text.in_force ? (
        <>
          {' · '}
          <strong>{`No longer in force; the current version is of ${formatDate(text.in_force)}.`}</strong>
        </>
      ) : null}
      {text.is_translation ? null : (
        <>
          {' · '}
          {text.english_url ? (
            <Anchor component={AppLink} to={text.english_url} size="sm">
              English translation
            </Anchor>
          ) : text.english_elsewhere ? (
            'No English translation of this version yet.'
          ) : (
            'In German only.'
          )}
        </>
      )}
      {' · '}
      <PdfLink href={text.pdf_url}>{text.pdf_has_english ? 'PDF, German and English' : 'PDF'}</PdfLink>
    </Text>
  );
}

/** The text itself, with what version it is. */
export function LegalTextBody({ text }: { text: LegalTextData }) {
  return (
    <Stack gap="sm" lang={text.language}>
      {text.is_translation ? (
        <Alert color="brand" variant="light" lang="en">
          This is an English translation for convenience. It may contain mistakes. Where it differs from the
          German version, the German version applies.{' '}
          <Anchor component={AppLink} to={text.german_url} size="sm">
            Read the German version
          </Anchor>
        </Alert>
      ) : null}
      <Title order={2}>{text.title}</Title>
      <Facts text={text} />
      {/* Markdown rendered on the server with HTML switched off: nothing in it can run. */}
      <div className={classes.text} dangerouslySetInnerHTML={{ __html: text.html }} />
    </Stack>
  );
}

/** The text on a page of its own: beside it its contents and the other versions. */
export function LegalTextPage({ text }: { text: LegalTextData }) {
  return (
    <div className={classes.layout}>
      <Panel title={text.is_translation ? 'English translation' : 'Text'}>
        <LegalTextBody text={text} />
      </Panel>
      {text.contents.length || text.others.length ? (
        <Stack gap="lg" className={classes.aside}>
          {text.contents.length ? (
            <Panel title="Contents">
              <List listStyleType="none" spacing={4} size="sm">
                {text.contents.map((section) => (
                  <List.Item key={section.anchor} className={section.level === 1 ? classes.part : undefined}>
                    <Anchor href={`#${section.anchor}`} size="sm">
                      {section.label}
                    </Anchor>
                  </List.Item>
                ))}
              </List>
            </Panel>
          ) : null}
          {text.others.length ? (
            <Panel title="Other versions">
              <List listStyleType="none" spacing={4} size="sm">
                {text.others.map((other) => (
                  <List.Item key={other.version}>
                    <Anchor component={AppLink} to={other.url} size="sm">
                      {formatDate(other.version)}
                      {other.in_force ? ' (in force)' : ''}
                    </Anchor>
                  </List.Item>
                ))}
              </List>
            </Panel>
          ) : null}
        </Stack>
      ) : null}
    </div>
  );
}
