"""The page for deciding what the new forum's categories should be.

A spreadsheet is the wrong shape for this job. The question is not "fill in a
column" but "is this 2016 lecture the same course as this one, and do I still
care" -- which is answered by looking at what is actually in the old forum,
and by holding it against a curriculum that has to be written down first.

So this writes one self-contained page with the archive's own data in it. It
opens from a file, keeps what has been decided in the browser, and hands it
back as JSON when the work is done. Nothing it holds leaves the machine it is
opened on, which is the other reason not to make it a website.
"""

import json
from datetime import datetime, timezone

PAGE = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forum Categories</title>
<style>
:root {
  --bg: #12151a; --panel: #1a1e26; --line: #2b3240; --ink: #e6e9ef;
  --dim: #96a0b5; --accent: #4ea1d3; --warn: #d39a4e; --good: #6cc08b;
  --archive: #7a5ea8;
}
:root:not([data-theme="dark"]) {
  --bg: #f6f7f9; --panel: #fff; --line: #dfe3ea; --ink: #1b1f27;
  --dim: #5d6779; --accent: #1d6fa5;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
header {
  position: sticky; top: 0; z-index: 5; background: var(--panel);
  border-bottom: 1px solid var(--line); padding: 12px 16px;
  display: flex; gap: 16px; align-items: center; flex-wrap: wrap;
}
h1 { font-size: 17px; margin: 0; font-weight: 600; }
.grow { flex: 1; }
.count { color: var(--dim); font-size: 13px; }
.bar { height: 6px; background: var(--line); border-radius: 3px; width: 160px; }
.bar > i { display: block; height: 100%; background: var(--good);
           border-radius: 3px; transition: width .2s; }
button, select, input, textarea {
  font: inherit; color: var(--ink); background: var(--bg);
  border: 1px solid var(--line); border-radius: 6px; padding: 5px 9px;
}
button { cursor: pointer; }
button:hover { border-color: var(--accent); }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
main { display: grid; grid-template-columns: 340px 1fr; gap: 16px; padding: 16px;
       align-items: start; }
@media (max-width: 900px) { main { grid-template-columns: 1fr; } }
section { background: var(--panel); border: 1px solid var(--line);
          border-radius: 10px; padding: 14px; }
section > h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em;
               color: var(--dim); margin: 0 0 10px; font-weight: 600; }
.side { position: sticky; top: 64px; }
textarea { width: 100%; min-height: 150px; font-family: ui-monospace, monospace;
           font-size: 13px; resize: vertical; }
.hint { color: var(--dim); font-size: 12px; margin: 6px 0 10px; }
.sem { margin-bottom: 8px; }
.sem > b { display: block; font-size: 13px; color: var(--accent); }
.sem ul { margin: 2px 0 0; padding-left: 16px; color: var(--dim); font-size: 13px; }
.group { border-top: 1px solid var(--line); padding-top: 12px; margin-top: 12px; }
.group:first-of-type { border-top: 0; margin-top: 0; padding-top: 0; }
.grouphead { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap;
             margin-bottom: 6px; }
.grouphead b { font-size: 15px; }
.row { display: grid; grid-template-columns: 1fr auto; gap: 10px;
       padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px;
       margin-bottom: 6px; background: var(--bg); }
.row.decided { border-color: var(--good); }
.row.archived { border-color: var(--archive); }
.name { font-weight: 600; }
.meta { color: var(--dim); font-size: 12.5px; }
.meta .old { opacity: .75; }
.stale { color: var(--warn); }
.live { color: var(--good); }
details { margin-top: 6px; }
summary { cursor: pointer; color: var(--accent); font-size: 12.5px; }
.samples { margin: 6px 0 0; padding-left: 16px; font-size: 12.5px;
           color: var(--dim); font-family: ui-monospace, monospace;
           word-break: break-all; }
.controls { display: flex; flex-direction: column; gap: 5px; align-items: stretch; }
.controls select { min-width: 210px; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
</style>
</head>
<body>
<header>
  <h1>Forum Categories</h1>
  <span class="count" id="tally"></span>
  <div class="bar"><i id="progress"></i></div>
  <span class="grow"></span>
  <button id="theme">Light / dark</button>
  <button id="reset">Start again</button>
  <button class="primary" id="export">Export JSON</button>
  <button id="exportcsv">Export CSV</button>
</header>

<main>
  <section class="side">
    <h2>The curriculum</h2>
    <p class="hint">
      One lecture per line, as <code>Semester / Lecture</code>. This is the
      structure the new forum gets, so write it as it should read.
    </p>
    <textarea id="curriculum" spellcheck="false" placeholder="Bachelor 1. Semester / Technisches Programmieren 1
Bachelor 1. Semester / Luftfahrtrecht
Bachelor 2. Semester / Mechanik 1
Master 1. Semester / Aerodynamik"></textarea>
    <p class="hint" id="curriculumcount"></p>
    <div id="tree"></div>
  </section>

  <section>
    <h2>The old board</h2>
    <div class="filters">
      <input id="search" placeholder="Search names and files" style="flex:1;min-width:180px">
      <select id="show">
        <option value="all">Everything</option>
        <option value="undecided">Not yet decided</option>
        <option value="assigned">Assigned to a lecture</option>
        <option value="archive">Archive</option>
      </select>
      <select id="order">
        <option value="course">By course name</option>
        <option value="recent">Most recent first</option>
        <option value="size">Biggest first</option>
      </select>
    </div>
    <div class="filters">
      <label class="hint" style="flex:1">
        Anything last posted in before
        <input id="cutoff" type="number" value="2021" min="2005" max="2030"
               style="width:6em">
        is a lecture that no longer runs.
      </label>
      <button id="archiveold" type="button">Archive all of those</button>
    </div>
    <div id="rows"></div>
  </section>
</main>

<script>
const DATA = __DATA__;
const KEY = "forum-categories:" + DATA.forum;
let decisions = {};
try { decisions = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
let curriculum = "";
try { curriculum = localStorage.getItem(KEY + ":curriculum") || ""; } catch (e) {}

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function save() {
  try {
    localStorage.setItem(KEY, JSON.stringify(decisions));
    localStorage.setItem(KEY + ":curriculum", $("curriculum").value);
  } catch (e) {}
}

function lectures() {
  const out = [];
  for (const line of $("curriculum").value.split("\\n")) {
    const text = line.trim();
    if (!text) continue;
    const cut = text.lastIndexOf("/");
    const semester = cut < 0 ? "" : text.slice(0, cut).trim();
    const lecture = cut < 0 ? text : text.slice(cut + 1).trim();
    if (lecture) out.push({ semester, lecture, full: text });
  }
  return out;
}

function drawTree() {
  const all = lectures();
  $("curriculumcount").textContent =
    all.length ? all.length + " lectures in " +
      new Set(all.map((l) => l.semester)).size + " semesters" : "";
  const bySemester = new Map();
  for (const one of all) {
    if (!bySemester.has(one.semester)) bySemester.set(one.semester, []);
    bySemester.get(one.semester).push(one.lecture);
  }
  $("tree").innerHTML = [...bySemester].map(([semester, names]) =>
    '<div class="sem"><b>' + esc(semester || "(no semester)") + "</b><ul>" +
    names.map((n) => "<li>" + esc(n) + "</li>").join("") + "</ul></div>"
  ).join("");
}

function groupsOf(rows) {
  const by = new Map();
  for (const row of rows) {
    if (!by.has(row.course)) by.set(row.course, []);
    by.get(row.course).push(row);
  }
  return by;
}

// The lecturer's name is in the subjects and the filenames and nowhere else,
// so the names that appear early against the names that appear lately are the
// evidence for whether a course changed hands -- which is what decides whether
// its old exams are still worth putting in front of anybody. A guess, and
// labelled as one, but it answers in a glance what otherwise means opening
// every exam in the lecture and comparing them.
function names(row) {
  const then = row.names_then || [], now = row.names_now || [];
  if (!then.length && !now.length) return "";
  const left = then.filter((n) => !now.includes(n));
  const kept = then.filter((n) => now.includes(n));
  const parts = [];
  if (now.length) parts.push('lately: <b>' + now.map(esc).join(", ") + "</b>");
  if (left.length) parts.push('earlier only: <span class="stale">' +
                              left.map(esc).join(", ") + "</span>");
  if (kept.length && left.length) parts.push("throughout: " + kept.map(esc).join(", "));
  return '<div class="meta names" title="Names found in the subjects and ' +
         'filenames. A guess, not a record.">names &mdash; ' +
         parts.join(" &middot; ") + "</div>";
}

function draw() {
  const options = lectures();
  const needle = $("search").value.trim().toLowerCase();
  const show = $("show").value;
  const order = $("order").value;

  let rows = DATA.rows.filter((row) => {
    const decided = decisions[row.old_fid];
    if (show === "undecided" && decided !== undefined) return false;
    if (show === "assigned" && (!decided || decided === "ARCHIVE")) return false;
    if (show === "archive" && decided !== "ARCHIVE") return false;
    if (!needle) return true;
    return (row.lecture + " " + row.old_path + " " +
            row.files.join(" ") + " " + row.subjects.join(" "))
           .toLowerCase().includes(needle);
  });

  if (order === "recent") rows = [...rows].sort((a, b) => b.last_post.localeCompare(a.last_post));
  else if (order === "size") rows = [...rows].sort((a, b) => b.posts - a.posts);

  const out = [];
  for (const [course, inGroup] of groupsOf(rows)) {
    const newest = inGroup.reduce((a, b) => (a.last_post > b.last_post ? a : b));
    out.push('<div class="group"><div class="grouphead">' +
      "<b>" + esc(newest.lecture) + "</b>" +
      '<span class="meta">' + inGroup.length + " version" +
      (inGroup.length > 1 ? "s" : "") + "</span>" +
      (inGroup.length > 1
        ? '<select data-group="' + esc(course) + '" class="bulk">' +
          '<option value="">assign all of them…</option>' +
          '<option value="ARCHIVE">Archive</option>' +
          options.map((o) => '<option value="' + esc(o.full) + '">' +
                             esc(o.full) + "</option>").join("") + "</select>"
        : "") +
      "</div>");

    for (const row of inGroup) {
      const chosen = decisions[row.old_fid];
      const cls = chosen === "ARCHIVE" ? "archived" : (chosen ? "decided" : "");
      const quiet = row.last_post < "2022";
      out.push('<div class="row ' + cls + '">' +
        "<div>" +
          '<div class="name">' + esc(row.lecture) + "</div>" +
          '<div class="meta"><span class="old">' + esc(row.old_path) + "</span></div>" +
          '<div class="meta">' + row.threads + " threads, " + row.posts +
            " posts &middot; " + esc(row.first_post) + " to " +
            '<span class="' + (quiet ? "stale" : "live") + '">' +
            esc(row.last_post) + "</span></div>" +
          names(row) +
          (row.subjects.length || row.files.length
            ? "<details><summary>What is in it</summary><ul class=\\"samples\\">" +
              row.subjects.map((t) => "<li>" + esc(t) + "</li>").join("") +
              row.files.map((f) => "<li>&#128206; " + esc(f) + "</li>").join("") +
              "</ul></details>"
            : "") +
        "</div>" +
        '<div class="controls">' +
          '<select data-fid="' + esc(row.old_fid) + '" class="pick">' +
            '<option value="">— not decided —</option>' +
            '<option value="ARCHIVE"' +
              (chosen === "ARCHIVE" ? " selected" : "") + ">Archive</option>" +
            options.map((o) => '<option value="' + esc(o.full) + '"' +
              (chosen === o.full ? " selected" : "") + ">" +
              esc(o.full) + "</option>").join("") +
          "</select>" +
        "</div>" +
      "</div>");
    }
    out.push("</div>");
  }

  $("rows").innerHTML = out.join("") ||
    '<p class="hint">Nothing matches that.</p>';

  const done = DATA.rows.filter((r) => decisions[r.old_fid] !== undefined).length;
  $("tally").textContent = done + " of " + DATA.rows.length + " decided";
  $("progress").style.width = (100 * done / DATA.rows.length) + "%";
}

// Most of a decade-old board is lectures that stopped running, and deciding
// those one at a time is the bulk of the work for none of the judgement. One
// button, one number, and what is left is the part that actually needs
// somebody who knows the curriculum.
function archiveEverythingOlderThan(year) {
  const caught = DATA.rows.filter((row) => (row.last_year || 0) < year
                                           && decisions[row.old_fid] === undefined);
  if (!caught.length) {
    alert("Nothing is undecided and older than " + year + ".");
    return;
  }
  if (!confirm("Put " + caught.length + " old forums into the archive?\n\n" +
               "Only ones you have not decided yet. You can still change any " +
               "of them afterwards.")) return;
  for (const row of caught) decisions[row.old_fid] = "ARCHIVE";
  save(); draw();
}

function download(name, text, type) {
  const blob = new Blob([text], { type: type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

document.addEventListener("click", (event) => {
  if (event.target.id === "archiveold") {
    archiveEverythingOlderThan(parseInt($("cutoff").value, 10) || 0);
  }
});

document.addEventListener("change", (event) => {
  const pick = event.target.closest(".pick");
  if (pick) {
    const value = pick.value;
    if (value) decisions[pick.dataset.fid] = value;
    else delete decisions[pick.dataset.fid];
    save(); draw(); return;
  }
  const bulk = event.target.closest(".bulk");
  if (bulk && bulk.value) {
    for (const row of DATA.rows) {
      if (row.course === bulk.dataset.group) decisions[row.old_fid] = bulk.value;
    }
    save(); draw(); return;
  }
  if (event.target === $("show") || event.target === $("order")) draw();
});

$("curriculum").addEventListener("input", () => { save(); drawTree(); draw(); });
$("search").addEventListener("input", draw);

$("export").addEventListener("click", () => {
  download("forum-categories.json", JSON.stringify({
    forum: DATA.forum,
    written_at: new Date().toISOString(),
    curriculum: lectures(),
    mapping: DATA.rows.map((row) => ({
      old_fid: row.old_fid,
      old_path: row.old_path,
      lecture: row.lecture,
      threads: row.threads,
      posts: row.posts,
      last_post: row.last_post,
      target: decisions[row.old_fid] || "ARCHIVE",
      decided: decisions[row.old_fid] !== undefined,
    })),
  }, null, 2), "application/json");
});

$("exportcsv").addEventListener("click", () => {
  const cell = (v) => '"' + String(v).replace(/"/g, '""') + '"';
  const lines = [["old_fid", "old_path", "lecture", "threads", "posts",
                  "last_post", "target", "decided"].join(",")];
  for (const row of DATA.rows) {
    lines.push([row.old_fid, row.old_path, row.lecture, row.threads, row.posts,
                row.last_post, decisions[row.old_fid] || "ARCHIVE",
                decisions[row.old_fid] !== undefined].map(cell).join(","));
  }
  download("forum-categories.csv", "\\uFEFF" + lines.join("\\n"), "text/csv");
});

$("reset").addEventListener("click", () => {
  if (!confirm("Forget every decision made on this page?")) return;
  decisions = {}; save(); draw();
});

$("theme").addEventListener("click", () => {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
});

$("curriculum").value = curriculum;
drawTree();
draw();
</script>
</body>
</html>
"""


def render_worksheet(rows, forum, sortable):
    """The page, with this board's own data in it."""
    payload = {
        "forum": forum,
        "written_at": datetime.now(tz=timezone.utc).isoformat(),
        "rows": [dict(row, course=sortable(row["lecture"])) for row in rows],
    }
    # </script> inside a string would end the block it sits in.
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return PAGE.replace("__DATA__", data)
