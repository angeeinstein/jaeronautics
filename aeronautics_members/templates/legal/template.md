{#- The template offered on Admin -> Legal Texts: the structure a legal text
    needs and everything its Markdown can do. Rendered with today's date, so it
    previews as it is. Not a Jinja page otherwise: no other braces below. -#}
---
# The front matter: between the two --- lines, never shown in the text.
title: "Mustertext"              # the title, as shown and in the PDF's name
document: "statutes"             # the text's folder: statutes, rules-of-procedure, membership-terms,
                                 # privacy-policy, webshop-event-terms, legal-notice -- or, for a
                                 # team's rules, team-rules together with the team line below
language: "de"                   # de for the text that applies, en for its translation
version: "{{ day }}"             # the version's day; the file is named after it: {{ day }}.md
effective_from: "{{ day }}"      # the day it applies from; may be later than the version
status: "draft"                  # draft (not shown) or published
source_revision: "Rev 1"         # optional: the text's own revision, shown beside the date
# team: "rocket-team"            # only for a team's rules: the team's short name
---

This template shows the structure of a legal text and everything its Markdown can do. Upload it on Admin → Legal Texts as it is to see the PDF; replace the content with your text. The file goes to legal/<document>/<language>/<version>.md -- a team's rules to legal/teams/<team>/team-rules/<language>/<version>.md. An English translation is a second file with the same version and document, language "en".

A paragraph is lines of text with an empty line before and after. Lines directly below each other are joined into one paragraph.  
To start a new line without a new paragraph, end the line before with two spaces -- as this one does -- or with a backslash.\
Like this one.

## § 1 Headings

A line starting with `## ` is a heading. Each one is listed in the contents, on the page and in the PDF. Starting with `## § 1`, it can be linked to as `#paragraph-1`: see [§ 2](#paragraph-2).

### A smaller heading

A line starting with `### ` is a heading below that, not listed in the contents.

## § 2 Emphasis and links

Text can be **bold** (`**bold**`) or *italic* (`*italic*`), and `code` for an address or a file name (between backticks).

A link: [the association's website](https://www.joanneum-aeronautics.at) is written `[text](https://…)`, an email address as `[office@example.org](mailto:office@example.org)`, or plainly <https://www.joanneum-aeronautics.at> between `<` and `>`.

## § 3 Lists

A numbered list, each line starting with a number and a full stop:

1. The first point.
2. The second point, which may run over several lines in the file; it stays one point.
3. The third point, with points of its own, indented by three spaces:
   - a point within it,
   - and another.

A list with dashes:

- one,
- two.

Lettered points under a numbered one are not a Markdown list. Each needs the line before it to end with two spaces, or it is joined to that line (the build checks this):

1. The members are:  
   a. ordinary members,  
   b. extraordinary members,  
   c. honorary members.

## § 4 Tables

A table: a header row, a row of dashes, then the rows; the cells divided by `|`. A colon on the right of the dashes aligns that column right, on both sides centres it.

| Kategorie | Beitrag | Fällig |
|---|---:|:---:|
| Studierende | € 15,00 | 1. Jänner |
| Alumni | € 25,00 | 1. Jänner |

## § 5 Quotes and lines

> A line starting with `> ` is set apart, as a note or a quotation.

A line of three dashes on its own, with empty lines around it, draws a line across:

---

# Teil B – Parts

A line starting with a single `# ` divides the text into parts ("Teil A", "Teil B"), shown larger and in the contents. A `# ` heading at the very top of the text is left out: the title comes from the front matter.

## § 6 What does not work

HTML in the text is shown as text, never run: <b>this</b> stays as typed. Pictures (`![…](…)`) are not shown in the PDF. Footnotes and crossed-out text are not supported.
