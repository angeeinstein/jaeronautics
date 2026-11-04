/**
 * The Markdown of a legal text, written in CodeMirror 6 (docs/legal-texts-in-portal-plan.md,
 * "The editor"): line numbers, Markdown highlighting, undo and redo, search
 * (Ctrl+F), lines wrapped. Uncontrolled: it starts with ``initial`` and
 * reports each change; the page changes its text through the view
 * (``replaceText``), so that a change from outside -- an earlier state put
 * back -- is undoable like typing.
 */
import { defaultKeymap, history, historyKeymap, indentWithTab, redo, undo } from '@codemirror/commands';
import { markdown } from '@codemirror/lang-markdown';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { highlightSelectionMatches, search, searchKeymap } from '@codemirror/search';
import { EditorSelection, EditorState } from '@codemirror/state';
import {
  drawSelection,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from '@codemirror/view';
import { tags } from '@lezer/highlight';
import { useEffect, useLayoutEffect, useRef } from 'react';

import classes from './MarkdownEditor.module.css';

const highlighting = HighlightStyle.define([
  { tag: tags.heading1, fontWeight: '700', color: 'var(--ja-accent-text)' },
  { tag: tags.heading2, fontWeight: '700', color: 'var(--ja-accent-text)' },
  { tag: tags.heading3, fontWeight: '600', color: 'var(--ja-accent-text)' },
  { tag: tags.strong, fontWeight: '700' },
  { tag: tags.emphasis, fontStyle: 'italic' },
  { tag: tags.link, color: 'var(--ja-link)' },
  { tag: tags.url, color: 'var(--ja-muted)' },
  { tag: tags.monospace, color: 'var(--ja-info)' },
  { tag: tags.quote, color: 'var(--ja-muted)' },
  { tag: [tags.processingInstruction, tags.contentSeparator, tags.list], color: 'var(--ja-muted)' },
]);

const theme = EditorView.theme(
  {
    '&': {
      color: 'var(--ja-text)',
      backgroundColor: 'var(--ja-surface-input)',
      border: '1px solid var(--ja-border)',
      fontSize: '0.9rem',
    },
    '&.cm-focused': { outline: '1px solid var(--ja-accent)' },
    '.cm-content': { fontFamily: 'var(--mantine-font-family-monospace)', caretColor: 'var(--ja-accent)' },
    '.cm-gutters': {
      backgroundColor: 'var(--ja-surface)',
      color: 'var(--ja-muted)',
      borderRight: '1px solid var(--ja-border)',
    },
    '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'var(--ja-surface-alt)' },
    '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': {
      backgroundColor: 'color-mix(in srgb, var(--ja-accent) 30%, transparent)',
    },
    '.cm-panels': { backgroundColor: 'var(--ja-surface)', color: 'var(--ja-text)' },
    '.cm-searchMatch': { backgroundColor: 'color-mix(in srgb, var(--ja-warning) 35%, transparent)' },
  },
  { dark: true },
);

export function MarkdownEditor({
  initial,
  onChange,
  viewRef,
  label,
  lang,
  readOnly = false,
}: {
  initial: string;
  onChange: (text: string) => void;
  /** The view, for the toolbar and for putting an earlier state back. */
  viewRef: React.RefObject<EditorView | null>;
  label: string;
  lang: string;
  readOnly?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const changed = useRef(onChange);
  useLayoutEffect(() => {
    changed.current = onChange;
  });

  useEffect(() => {
    if (!host.current) return undefined;
    const view = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: initial,
        extensions: [
          lineNumbers(),
          highlightActiveLineGutter(),
          highlightActiveLine(),
          history(),
          drawSelection(),
          search({ top: true }),
          highlightSelectionMatches(),
          keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]),
          markdown(),
          syntaxHighlighting(highlighting),
          EditorView.lineWrapping,
          EditorState.readOnly.of(readOnly),
          EditorView.contentAttributes.of({ 'aria-label': label, lang, spellcheck: 'true' }),
          theme,
          EditorView.updateListener.of((update) => {
            if (update.docChanged) changed.current(update.state.doc.toString());
          }),
        ],
      }),
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // Made once: the text changes through the view from then on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div ref={host} className={classes.editor} />;
}

/** The whole text replaced, as one change that undo takes back. */
export function replaceText(view: EditorView | null, text: string) {
  if (!view) return;
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } });
}

export function undoIn(view: EditorView | null) {
  if (view) undo(view);
}

export function redoIn(view: EditorView | null) {
  if (view) redo(view);
}

/** The selection between ``before`` and ``after`` (bold, italic, a link); with nothing chosen, the cursor between them. */
export function wrap(view: EditorView | null, before: string, after = before) {
  if (!view) return;
  view.dispatch(
    view.state.changeByRange((range) => ({
      changes: [
        { from: range.from, insert: before },
        { from: range.to, insert: after },
      ],
      range: EditorSelection.range(range.from + before.length, range.to + before.length),
    })),
  );
  view.focus();
}

/** Each chosen line starting with ``prefix`` (a heading, a list point), or no longer if all do. */
export function prefixLines(view: EditorView | null, prefix: string) {
  if (!view) return;
  const { state } = view;
  const lines = new Set<number>();
  for (const range of state.selection.ranges) {
    for (
      let number = state.doc.lineAt(range.from).number;
      number <= state.doc.lineAt(range.to).number;
      number++
    ) {
      lines.add(number);
    }
  }
  const all = [...lines].map((number) => state.doc.line(number));
  const remove = all.every((line) => line.text.startsWith(prefix));
  view.dispatch({
    changes: all.map((line) =>
      remove
        ? { from: line.from, to: line.from + prefix.length, insert: '' }
        : { from: line.from, insert: prefix },
    ),
  });
  view.focus();
}
