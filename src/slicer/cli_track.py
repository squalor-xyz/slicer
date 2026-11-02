"""Tracking-write commands: filing, editing, claiming and closing items, notes, feedback and import.

These change the roadmap's state, so `build_parser` marks them as mutating and `main` runs
each under the project lock. The reads that rank or list items are in `cli_queue`. Each
handler is attached to its subparser in `cli_parser`; shared helpers come from `cli_common`,
and nothing here imports `cli`. Tests patch `_read_user_file`, so it is called as
`cli_common._read_user_file`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from slicer import cli_common, feedback, github, ids, jsonio, land, migrator, model, ops, outline
from slicer import queries, render, store, vcs
from slicer.cli_common import (
  DRIFT, OK, _batch_ids, _begin_write, _body_from, _emit, _emit_items, _id_floor, _item_lookup,
  _mutating, _post_check, _render_hint, _state, _status_tally, _undeclared_pass_line, _write_err,
)
from slicer.config import CONFIG_NAME, Config
from slicer.errors import StateError


SKELETON_HEAD = """\
<!--
  A slicer outline. Each `## ` heading is one roadmap item.

  Optional key lines go directly under the heading:
    key:      case-sensitive filing identity; a retry returns the existing item
    short_title: the roadmap row's short title (default: the heading)
    size:     {sizes}
    tree:     which part of the codebase; comma-separated for several
              (`trees:` is the same key)
    findings: a reference back to whatever raised this
    status:   {statuses}
    pass:     a group key, if the project uses passes
    group:    a phase label rendered above the item
    discovered_from: the ID of the existing item whose work raised this one
    parent:   the ID of an existing item, or the title of another entry here, that
              this one is a child of
    depends:  the title of another item, here or already in the roadmap, or the ID of
              one already there; a title containing commas is matched whole, otherwise
              commas separate several, and repeated lines add more
    importance: 1, 2 or 3; how important (default 2)
    urgency:    1, 2 or 3; how urgent (default 2)
    effort:     1, 2 or 3; optional, omit to leave unset

  A paragraph after the keys and before the first `###` becomes the slice's
  lead. A lead paragraph that starts with the project's boundary marker is
  the scope boundary, not lead prose; put that paragraph in the lead or in
  one section, not both. Each `### ` heading is a section of the slice; an
  item with no sections is a roadmap row only.

  This project's configured sections are:
    {sections}

  Import with:  slicer import THIS-FILE.md --dry-run
-->

# Roadmap
"""


SKELETON_EXAMPLE = """
## Parse the config file
size: {size}
tree: core
findings: G1
importance: 3
urgency: 1
effort: 1

The loader accepts a missing key and carries on with a zero, so a typo in
the config reads as a deliberate setting.

{sections}
"""


def cmd_import(args: argparse.Namespace) -> int:
  if getattr(args, "legacy_from", None) is not None:
    raise StateError(
      "--from belongs to `slicer migrate`, which converts an existing markdown "
      "slice tree; `slicer import` takes an outline file",
      code="wrong_command",
    )
  state = _state(args)
  cfg = state.config

  if args.skeleton:
    sizes = "S, M or L by convention; anything you like"
    statuses = ", ".join(sorted(cfg.statuses))
    head = SKELETON_HEAD.format(
      sizes=sizes, statuses=statuses, sections=", ".join(cfg.sections) or "(none)"
    )
    body = SKELETON_EXAMPLE.format(
      size=(cfg.sections and "M") or "M",
      sections="\n\n".join(f"### {h}" for h in cfg.sections),
    )
    _emit(args, {"skeleton": head + body}, head + body)
    return OK

  if not args.file:
    raise StateError("give an outline file, or --skeleton to print a template", code="usage")

  path = Path(args.file)
  if not path.is_absolute():
    path = Path(args.root or ".").resolve() / path
  parsed = outline.parse(cli_common._read_user_file(path), path=str(path))
  specs = parsed.items
  preamble = parsed.preamble or None

  if args.dry_run:
    report = ops.outline_report(
      state, specs, force=args.force, preamble=preamble, id_floor=_id_floor(state),
    )
  else:
    report = ops.apply_outline(
      state, specs, force=args.force, preamble=preamble, id_floor=_id_floor(state),
    )

  args._no_change = not report.created and not report.problems
  lines = [
    f"source     {path}",
    f"outline    {report.items} items, {report.promoted} with slices",
    "status     " + _status_tally(state.config, report.by_status),
    f"depends    {report.depends_edges} edges",
  ]
  if report.preamble is not None:
    lines.append("preamble   " + report.preamble.replace("\n", " "))
  if report.off_schema_sections:
    lines.append(
      f"sections   {len(report.off_schema_sections)} off-schema: "
      + ", ".join(f"{k} {v}" for k, v in report.off_schema_sections.items())
    )
  for w in report.warnings:
    lines.append(f"warn       {w}")
  for pr in report.problems:
    lines.append(f"PROBLEM    {pr}")

  if report.problems:
    lines.append("refusing to write: fix the problems above, or re-run with --dry-run to inspect")
    _emit(args, report.to_dict(), "\n".join(lines))
    return DRIFT
  lines.append(f"added      {', '.join(report.ids)}")
  if args.dry_run:
    lines.append("nothing written; drop --dry-run to apply")
    _emit(args, report.to_dict(), "\n".join(lines))
    return OK

  if _render_hint(args):
    lines.append(f"now {_render_hint(args)}")
  _emit(args, report.to_dict(), "\n".join(lines))
  filed = [state.index.require(entry["id"]) for entry in report.entries if not entry["existing"]]
  for pass_key in state.index.undeclared_pass_keys(filed):
    _write_err(args, _undeclared_pass_line(pass_key))
  return OK


def cmd_migrate(args: argparse.Namespace) -> int:
  root = Path(args.root or ".").resolve()
  source = Path(args.source)
  if not source.is_absolute():
    source = root / source
  base = root / store.DIR_NAME
  cfg = Config.load(base / CONFIG_NAME) if (base / CONFIG_NAME).is_file() else Config()

  # migrate replaces config.json and index.json wholesale, so refuse to run it
  # over a roadmap that already has items -- one mistaken invocation would
  # destroy it. An empty init'd project has no items and no slices, which is
  # the normal "init then migrate" path, so that is allowed. --dry-run writes
  # nothing, so it stays a safe preview even on a non-empty project.
  index_path = base / store.INDEX_NAME
  existing = len(model.Index.from_dict(jsonio.read(index_path)).items) if index_path.is_file() else 0
  if existing and not args.force and not args.dry_run:
    raise StateError(
      f"{base} already has a roadmap ({existing} items); migrate would replace it "
      "-- pass --force to overwrite, or --dry-run to preview",
      code="already_exists",
    )

  render_root = base / store.RENDER_DIR
  index, slices, report = migrator.build(
    source,
    cfg,
    render_dir=render_root / render.SLICES_SUBDIR,
    index_render_dir=render_root,
  )
  lines = [
    f"source     {source}",
    f"index      {report.passes} passes, {report.groups} group rows, {report.items} items, next id {report.next_id}",
    f"status     " + _status_tally(cfg, report.by_status),
    f"sizes      " + " · ".join(f"{k} {v}" for k, v in report.by_size.items()),
    f"slices     {report.slices} parsed, {report.roundtrip_ok} round-trip byte-identical",
    f"sections   {len(report.off_schema_sections)} off-schema: "
    + ", ".join(f"{k} {v}" for k, v in list(report.off_schema_sections.items())[:6]),
    f"depends    {report.depends_edges} edges",
    f"reconcile  {report.title_differences} title, {report.findings_differences} findings, "
    f"{report.trees_differences} trees differences - both sides kept",
  ]
  for w in report.warnings:
    lines.append(f"warn       {w}")
  for p in report.problems:
    lines.append(f"PROBLEM    {p}")

  if report.problems:
    lines.append("refusing to write: fix the problems above, or re-run with --dry-run to inspect")
    _emit(args, report.to_dict(), "\n".join(lines))
    return DRIFT
  if args.dry_run:
    lines.append(f"would write {store.DIR_NAME}/ under {root} (nothing written)")
    _emit(args, report.to_dict(), "\n".join(lines))
    return OK

  written = migrator.write(root, cfg, index, slices)
  lines.append(f"wrote      {len(written)} files under {base}")
  _emit(args, report.to_dict(), "\n".join(lines))
  return OK


def cmd_next_id(args: argparse.Namespace) -> int:
  state = _state(args)
  item_id = ids.format_next(state.index, floor=_id_floor(state))
  _emit(args, {"id": item_id}, item_id)
  return OK


def cmd_id_prefix(args: argparse.Namespace) -> int:
  state = _state(args)
  if args.prefix is None:
    _emit(args, {"prefix": state.index.id_prefix}, state.index.id_prefix)
    return OK
  if args.dry_run:
    ids.check_prefix_change(state.index, args.prefix)
    old = state.index.id_prefix
    changed = (old, state.config.id_prefix) != (args.prefix, args.prefix)
  else:
    old, changed = ops.set_id_prefix(state, args.prefix)
  next_id = ids.format_id(args.prefix, state.index.next_id, state.index.id_width)
  payload = {"from": old, "to": args.prefix, "changed": changed, "next_id": next_id}
  if args.dry_run:
    payload["dry_run"] = True
  if not changed:
    text = f"id prefix is already {args.prefix}; next id {next_id}"
  else:
    verb = "would change" if args.dry_run else "changed"
    text = f"id prefix {verb} {old} -> {args.prefix}; next id {next_id}"
  _emit(args, payload, text)
  return OK


def cmd_add(args: argparse.Namespace) -> int:
  if args.print_id and args.json:
    raise StateError("--print-id cannot be combined with --json", code="usage")
  state = _state(args)
  key = ops.filing_key(args.key)
  existing = ops.keyed_item(state, key)
  if existing is not None:
    args._no_change = True
    _emit(args, existing.to_dict() | {"existing": True},
          existing.id if args.print_id else f"existing {existing.id}  {existing.display_title()}")
    return OK
  item = ops.add(
    state, args.title, item_id=args.id, size=args.size or "",
    trees=args.tree or [], findings=args.findings or "", status=args.status,
    pass_key=args.pass_key, importance=args.importance, urgency=args.urgency,
    effort=args.effort, depends_on=args.depends_on, short_title=args.short_title, key=args.key,
    id_floor=_id_floor(state), discovered_from=args.discovered_from,
    parent=args.parent,
  )
  text = item.id if args.print_id else f"added {item.id}  {item.display_title()}"
  if item.pass_key and not args.print_id:
    text += f" (pass: {item.pass_key})"
  _emit(args, item.to_dict() | {"existing": False}, text)
  if item.unscored and item.status in (state.config.open_status, state.config.started_status):
    _write_err(args, (
      f"slicer: {item.id} is unscored (importance 2, urgency 2, no effort), so it ranks on "
      f"nothing; pass --importance, --urgency and --effort, or `slicer set {item.id}` later."
    ))
  for pass_key in state.index.undeclared_pass_keys([item]):
    _write_err(args, _undeclared_pass_line(pass_key))
  return OK


def cmd_promote(args: argparse.Namespace) -> int:
  state = _state(args)
  source = None
  source_path = "<promote>"
  if args.file:
    source = cli_common._read_user_file(args.file)
    source_path = args.file
  elif args.stdin:
    source = sys.stdin.read()
    source_path = "<stdin>"
  sl = ops.promote(state, args.id, force=args.force, source=source, source_path=source_path, boundary=args.boundary)
  _emit(args, sl.to_dict(), f"promoted {sl.id} -> {state.slice_path(sl.id)}")
  return OK


def cmd_move(args: argparse.Namespace) -> int:
  state = _state(args)
  at = ops.move(state, args.id, before=args.before, after=args.after, to=args.to)
  _emit(args, {"id": args.id, "position": at}, f"{args.id} is now at position {at}")
  return OK


def cmd_sort(args: argparse.Namespace) -> int:
  state = _state(args)
  moved = ops.sort_queue(state, by=args.by)
  text = f"sorted by {args.by}: {moved} item(s) moved" if moved else f"already sorted by {args.by}"
  _emit(args, {"by": args.by, "moved": moved}, text)
  return OK


def cmd_set(args: argparse.Namespace) -> int:
  item_ids, batch = _batch_ids(args)
  state = _state(args)
  if args.no_effort and args.effort is not None:
    raise StateError("--effort and --no-effort cannot be combined", code="usage")
  if (args.add_flag or args.remove_flag) and (args.flag is not None or args.no_flags):
    raise StateError(
      "cannot combine --add-flag or --remove-flag with --flag or --no-flags",
      code="usage",
    )
  flags = [] if args.no_flags else args.flag
  fields = dict(
    title=args.title, short_title=args.short_title, status=args.status,
    size=args.size, trees=args.tree, findings=args.findings, depends_on=args.depends_on,
    pass_key=args.pass_key, flags=flags, group=args.group, parent=args.parent,
    discovered_from=args.discovered_from,
    importance=args.importance, urgency=args.urgency,
    effort=args.effort, attempts=args.attempts,
  )
  fields = {key: value for key, value in fields.items() if value is not None}
  if args.no_effort:
    fields["effort"] = None
  with _item_lookup(state, item_ids):
    items = ops.set_fields_many(
      state, item_ids, add_flags=args.add_flag, remove_flags=args.remove_flag, **fields,
    )
  _emit_items(args, items, batch, [f"updated {item.id}" for item in items])
  if args.pass_key:
    for pass_key in state.index.undeclared_pass_keys(items):
      _write_err(args, _undeclared_pass_line(pass_key))
  return OK


def cmd_decide(args: argparse.Namespace) -> int:
  state = _state(args)
  if sum((args.text is not None, args.file is not None, args.stdin)) != 1:
    raise StateError("give the answer with exactly one of --text, --file, or --stdin", code="usage")
  body = _body_from(args, "")
  item = ops.decide(state, args.id, body)
  hint = _render_hint(args)
  _emit(args, item.to_dict(), f"recorded decision for {item.id}" + (f"; {hint}" if hint else ""))
  return OK


def cmd_ready(args: argparse.Namespace) -> int:
  state = _state(args)
  with _item_lookup(state, [args.id]):
    item = ops.mark_ready(state, args.id, by=args.by, note=args.note or "")
  _emit(args, item.to_dict() | {"ready": True}, f"{item.id} ready, recorded by {item.readiness.by}")
  return OK


def cmd_note(args: argparse.Namespace) -> int:
  state = _state(args)
  body = _body_from(args, "")
  if body is None:
    raise StateError("editor exited non-zero; nothing added", code="editor_aborted")
  with _item_lookup(state, [args.id]):
    ops.add_note(state, args.id, body, kind=args.kind, owner=args.owner, stale_after=args.stale_after)
  hint = _render_hint(args)
  _emit(args, {"id": args.id, "added": True}, f"added note to {args.id}" + (f"; {hint}" if hint else ""))
  return OK


def cmd_feedback(args: argparse.Namespace) -> int:
  """Append to, print, or export the local feedback log. Not a roadmap write: it
  takes the project lock itself and never touches the index, history, or render."""
  if args.out is not None:
    if args.kind is not None or args.item is not None or args.text is not None \
        or args.file is not None or args.stdin:
      raise StateError("--out cannot be combined with --kind, --text, --file, --stdin, or --item",
        code="usage")
  elif args.force:
    raise StateError("--force is valid only with --out", code="usage")
  appending = args.kind is not None
  if not appending and args.out is None and (
      args.item is not None or args.text is not None or args.file is not None or args.stdin):
    raise StateError("--text, --file, --stdin, and --item need --kind", code="usage")
  body = None
  if appending:
    if sum((args.text is not None, args.file is not None, args.stdin)) != 1:
      raise StateError("give the feedback body with exactly one of --text, --file, or --stdin",
        code="usage")
    body = _body_from(args, "")
  root = store.discover(Path(args.root) if args.root else None)
  shown = f"{store.DIR_NAME}/{feedback.LOG_NAME}"
  with store.project_lock(root):
    if appending:
      entry = feedback.append(root, args.kind, body, args.item or None)
      _emit(args, {"path": shown, "entry": entry}, f"added feedback to {shown}")
    elif args.out is not None:
      dest = Path(args.out)
      feedback.export(root, dest, force=args.force)
      _emit(args, {"path": shown, "out": str(dest)}, f"wrote {shown} to {dest}")
    else:
      entries = feedback.read(root)
      log = feedback.log_path(root)
      text = log.read_bytes().decode("utf-8").rstrip("\n") if entries else f"no feedback entries in {shown}"
      _emit(args, {"path": shown, "entries": entries}, text)
  return OK


def cmd_feedback_report(args: argparse.Namespace) -> int:
  """File bug and feature entries from the local feedback log as GitHub issues.

  Not a roadmap write: it never touches the index, history, or render. It takes
  the project lock to read the entries and again to record each filed one, and
  never holds it across `gh`, which may wait on the network or a login prompt.
  """
  if args.dry_run == args.yes:
    raise StateError("pass exactly one of --dry-run (preview) or --yes (file the issues)",
      code="usage")
  kinds = list(dict.fromkeys(args.kind or ("bug", "feature")))
  unknown = [kind for kind in kinds if kind not in feedback.KINDS]
  if unknown:
    raise StateError(f"unknown feedback kind {unknown[0]!r}; choose from {', '.join(feedback.KINDS)}",
      code="usage")
  root = store.discover(Path(args.root) if args.root else None)
  repo = args.repo if args.repo is not None else Config.load(
    root / store.DIR_NAME / CONFIG_NAME).issues_repo
  if not repo:
    raise StateError("no destination repository: pass --repo OWNER/REPO or set issues_repo in "
      f"{store.DIR_NAME}/{CONFIG_NAME}", code="usage")
  github.require_repo(repo)
  shown = f"{store.DIR_NAME}/{feedback.LOG_NAME}"
  with store.project_lock(root):
    done = feedback.reported_hashes(root, repo)
    selected = [
      (digest, entry) for digest, entry in feedback.hashed(root)
      if digest not in done and entry["kind"] in kinds
      and (args.item is None or entry.get("item") == args.item)
    ]

  def shape(digest: str, entry: dict[str, str]) -> dict[str, object]:
    out: dict[str, object] = {"entry": digest, "kind": entry["kind"], "at": entry["at"]}
    if "item" in entry:
      out["item"] = entry["item"]
    out["title"] = feedback.issue_title(entry)
    return out

  if not selected:
    _emit(args, {"repo": repo, "dry_run": args.dry_run, "issues": [], "unmarked": []},
      f"nothing to file to {repo} from {shown}", keep_empty=("issues", "unmarked"))
    return OK
  if args.dry_run:
    issues = [{**shape(d, e), "body": feedback.issue_body(e)} for d, e in selected]
    blocks = [f"would file to {repo}: {issue['title']}\n{issue['body']}" for issue in issues]
    _emit(args, {"repo": repo, "dry_run": True, "issues": issues, "unmarked": []},
      "\n".join(blocks).rstrip("\n"), keep_empty=("unmarked",))
    return OK
  filed: list[dict[str, object]] = []
  unmarked: list[dict[str, object]] = []
  for position, (digest, entry) in enumerate(selected, 1):
    try:
      number, url = github.create_issue(repo, feedback.issue_title(entry), feedback.issue_body(entry))
    except StateError as exc:
      if exc.code != "external":
        raise
      earlier = ", ".join(str(issue["url"]) for issue in filed) or "none"
      raise StateError(
        f"stopped at entry {position} of {len(selected)} ({entry['kind']} at {entry['at']}): {exc}. "
        f"Filed and recorded before it: {earlier}",
        code="external",
      ) from None
    issue = {**shape(digest, entry), "issue": number, "url": url}
    # Record only an entry that still reads as it did when it was sent.
    with store.project_lock(root):
      current = {d for d, _ in feedback.hashed(root)}
      if digest in current:
        feedback.record(root, digest, repo, number, url)
        filed.append(issue)
      else:
        unmarked.append(issue)
        _write_err(args, f"slicer: filed {url}, but the {entry['kind']} entry at {entry['at']} "
          "changed while it was sent, so it is not marked; a later run may file it again")
  lines = [f"filed {issue['url']} {issue['title']}" for issue in filed + unmarked]
  _emit(args, {"repo": repo, "dry_run": False, "issues": filed, "unmarked": unmarked},
    "\n".join(lines), keep_empty=("issues", "unmarked"))
  return OK


def cmd_issues_pull(args: argparse.Namespace) -> int:
  """File a project's open GitHub issues as unscored roadmap rows, one per issue.

  A roadmap write, but not marked `mutates`: `main` would hold the project lock
  for the whole handler, and `gh` may wait on the network or a login prompt.
  The fetch runs unlocked; the write takes the lock, refuses a merge, reloads,
  and goes through `_mutating` so `--render` and `--strict` behave as for `add`.
  """
  if not 1 <= args.limit <= 100:
    raise StateError(f"--limit must be 1-100, not {args.limit}", code="usage")
  if args.strict and not (args.render and not args.dry_run):
    raise StateError("--strict has no effect without --render", code="usage")
  root = store.discover(Path(args.root) if args.root else None)
  repo = args.repo if args.repo is not None else Config.load(
    root / store.DIR_NAME / CONFIG_NAME).issues_repo
  if not repo:
    raise StateError("no source repository: pass --repo OWNER/REPO or set issues_repo in "
      f"{store.DIR_NAME}/{CONFIG_NAME}", code="usage")
  github.require_repo(repo)
  issues = github.list_open_issues(repo, args.limit)
  pulled = ops.pull_issues(store.load(root), repo, issues, write=False)
  if args.dry_run:
    _emit_pulled(args, repo, pulled)
    return OK
  args._pull = (repo, issues)
  with store.project_lock(root):
    _begin_write(args, root)
    return _issues_pull_write(args)


def _issues_pull_locked(args: argparse.Namespace) -> int:
  state = _state(args)
  repo, issues = args._pull
  pulled = ops.pull_issues(state, repo, issues, write=True)
  created = [entry["item"].id for entry in pulled if not entry["existing"]]
  if not created:
    args._no_change = True
  _emit_pulled(args, repo, pulled)
  if created:
    one = len(created) == 1
    _write_err(args, (
      f"slicer: {', '.join(created)} {'is' if one else 'are'} unscored (importance 2, urgency 2, "
      f"no effort), so {'it ranks' if one else 'they rank'} on nothing; score with "
      "`slicer set ID` later."
    ))
  return OK


_issues_pull_write = _mutating(_issues_pull_locked)


def _emit_pulled(args: argparse.Namespace, repo: str, pulled: list[dict[str, object]]) -> None:
  items = [
    {"number": entry["number"], "title": entry["title"], "key": entry["key"],
     "id": entry["item"].id if entry["item"] is not None else None, "existing": entry["existing"]}
    for entry in pulled
  ]
  # A dry run creates nothing, so `created` stays empty; `items` shows what it would file.
  created = [item["id"] for item in items if not item["existing"] and item["id"] is not None]
  reused = [item["id"] for item in items if item["existing"]]
  if args.dry_run:
    lines = [f"would file #{item['number']}  {item['title']}" for item in items if not item["existing"]]
  else:
    lines = [f"filed {item['id']}  #{item['number']}  {item['title']}" for item in items if not item["existing"]]
  lines += [f"reused {item['id']}  #{item['number']}  {item['title']}" for item in items if item["existing"]]
  if all(item["existing"] for item in items):
    lines.append(f"nothing new to file from {repo}")
  _emit(args, {"repo": repo, "dry_run": args.dry_run, "created": created, "reused": reused,
    "items": items}, "\n".join(lines), keep_empty=("created", "reused"))


def cmd_note_verify(args: argparse.Namespace) -> int:
  record, changed = ops.verify_note(_state(args), args.id, args.note_id,
    owner=args.owner, attest=args.command == "note-attest")
  if not changed:
    args._no_change = True
  _emit(args, {"id": args.id, "note_record": record.to_dict(), "trust": record.trust},
    f"{args.id} note {record.id}: {record.trust}" + ("" if changed else " (unchanged)"))
  return OK


def cmd_note_retract(args: argparse.Namespace) -> int:
  record = ops.retract_note(_state(args), args.id, args.note_id, args.reason, owner=args.owner)
  _emit(args, {"id": args.id, "note_record": record.to_dict()},
    f"{args.id} note {record.id}: retracted by {record.retracted_by}")
  return OK


def cmd_edit(args: argparse.Namespace) -> int:
  if sum((args.section is not None, args.boundary, args.lead)) != 1:
    raise StateError("choose exactly one of --section, --boundary, or --lead", code="usage")
  if args.lead and args.append:
    raise StateError("--append cannot be used with --lead", code="usage")
  state = _state(args)
  # Look the item up first: "S99 has no slice" is a confusing thing to say
  # about an item that does not exist at all.
  with _item_lookup(state, [args.id]):
    state.index.require(args.id)
  sl = state.slices.get(args.id)
  if sl is None:
    raise StateError(
      f"{args.id} has no slice; run `slicer promote {args.id}` first", code="no_slice"
    )
  if args.boundary and args.append:
    raise StateError("--append cannot be used with --boundary", code="usage")
  section = sl.section(args.section)
  if args.append and args.text is None and args.file is None and not args.stdin:
    raise StateError("--append requires --text, --file, or --stdin", code="usage")
  if args.lead:
    initial = "\n\n".join(sl.lead)
  else:
    initial = sl.boundary if args.boundary else (section.body if section else "")
  body = _body_from(args, initial)
  if body is None:
    raise StateError("editor exited non-zero; slice unchanged", code="editor_aborted")
  if args.lead:
    # The same blank-line split `promote` uses for a lead; an empty body clears it.
    lead = outline.blocks(body.split("\n"))
    ops.edit_lead(state, args.id, lead)
    _emit(args, {"id": args.id, "lead": lead}, f"updated {args.id} / lead")
  elif args.boundary:
    ops.edit_boundary(state, args.id, body)
    _emit(args, {"id": args.id, "boundary": body}, f"updated {args.id} / boundary")
  else:
    ops.edit_section(state, args.id, args.section, body, append=args.append)
    _emit(args, {"id": args.id, "section": args.section}, f"updated {args.id} / {args.section}")
  return OK


def _status_cmd(status_attr: str):
  def run(args: argparse.Namespace) -> int:
    item_ids, batch = _batch_ids(args)
    state = _state(args)
    status = getattr(state.config, status_attr)
    if not status and status_attr in ("parked_status", "started_status"):
      name = status_attr.removesuffix("_status")
      raise StateError(f"this project declares no {name} status; set {status_attr} in config")
    items = ops.set_status_many(state, item_ids, status, note=args.note or "")
    _emit_items(args, items, batch,
                [f"{item.id} -> {state.config.status_label(item.status)}" for item in items])
    return OK
  return run


cmd_park = _status_cmd("parked_status")


def cmd_start(args: argparse.Namespace) -> int:
  """Mark items started, then warn if another checkout already names one.

  The warning is stderr only. `next` does not call this, and a failed start
  does not warn. Ids are read once so a batch still loads and saves once.
  """
  item_ids, batch = _batch_ids(args)
  state = _state(args)
  status = state.config.started_status
  if not status:
    raise StateError("this project declares no started status; set started_status in config")
  with _item_lookup(state, item_ids):
    items = ops.start_many(state, item_ids, note=args.note or "", owner=args.owner)
  _emit_items(args, items, batch,
              [f"{item.id} -> {state.config.status_label(item.status)}" for item in items])
  for item_id in item_ids:
    names = vcs.elsewhere(state.root, item_id)
    if names:
      _write_err(args, f"slicer: {item_id} is also named by {', '.join(names)}")
  return OK


def cmd_release(args: argparse.Namespace) -> int:
  """Clear claims and leave status alone. Ids are read once."""
  item_ids, batch = _batch_ids(args)
  state = _state(args)
  items = ops.release_many(state, item_ids, owner=args.owner)
  _emit_items(args, items, batch, [f"{item.id} released" for item in items])
  return OK


def cmd_handoff(args: argparse.Namespace) -> int:
  """Hand started slices to review: status to review, claim cleared. Ids are read once."""
  item_ids, batch = _batch_ids(args)
  state = _state(args)
  items = ops.handoff_many(state, item_ids, note=args.note or "", owner=args.owner)
  _emit_items(args, items, batch,
              [f"{item.id} -> {state.config.status_label(item.status)}, unclaimed" for item in items])
  return OK


def cmd_reject(args: argparse.Namespace) -> int:
  """Send failed reviews back with a verdict: status to open (or --to), claim cleared."""
  item_ids, batch = _batch_ids(args)
  state = _state(args)
  items = ops.reject_many(state, item_ids, note=args.note, to=args.to, owner=args.owner)
  _emit_items(args, items, batch,
              [f"{item.id} -> {state.config.status_label(item.status)}, unclaimed" for item in items])
  return OK


def cmd_done(args: argparse.Namespace) -> int:
  if getattr(args, "check", False) and not args.render:
    raise StateError("--check has no effect without --render", code="usage")
  item_ids, batch = _batch_ids(args)
  state = _state(args)
  by = queries.actor(state, args.owner)
  if args.render:
    items, written = ops.done_and_render(state, item_ids, note=args.note or "", by=by)
  else:
    items = ops.set_status_many(
      state, item_ids, state.config.done_status, note=args.note or "", by=by
    )
  _emit_items(args, items, batch,
              [f"{item.id} -> {state.config.status_label(item.status)}" for item in items])
  if args.render and not args.json:
    print(f"rendered {written} file(s)")
  if getattr(args, "check", False):
    return _post_check(args, "done")
  return OK


cmd_done.mutates = True


def cmd_land(args: argparse.Namespace) -> int:
  """Check the slice's branch is merged and its relay runs ended, then `done --render --check`.

  Prints the git commands left; slicer commits, removes and deletes nothing itself.
  """
  state = _state(args)
  landing = land.land(state, args.id, args.branch, note=args.note, by=queries.actor(state, args.owner))
  lines = [
    f"{landing.item.id} -> {state.config.status_label(landing.item.status)}",
    f"rendered {landing.written} file(s)",
    "git commands left to run:",
    *landing.commands,
  ]
  _emit(args, landing.to_dict(), "\n".join(lines))
  return _post_check(args, "land")


cmd_land.mutates = True


def cmd_remove(args: argparse.Namespace) -> int:
  state = _state(args)
  if args.dry_run:
    preview = queries.remove_preview(state, args.id, purge=args.purge)
    payload = {
      "id": preview.id, "mode": preview.mode, "dry_run": True,
      "blockers": preview.blockers, "file": preview.file,
      "id_freed": preview.id_freed, "reason": preview.reason,
    }
    verb = "purged" if args.purge else "retired"
    lines = [f"{preview.id} would be {verb} (dry run)"]
    if args.purge:
      lines.append(f"     file: {preview.file}" if preview.file else "     no slice file")
      lines.append(f"     id would be {'freed' if preview.id_freed else 'kept burned'}: {preview.reason}")
    else:
      lines.append("     id stays claimed; slice would move to the retired folder")
    if preview.blockers:
      lines.append("     blockers: " + "; ".join(preview.blockers) + " (pass --force)")
    _emit(args, payload, "\n".join(lines))
    return OK
  if args.purge:
    result = ops.purge(state, args.id, force=args.force)
    freed = "freed" if result.id_freed else "kept burned"
    _emit(
      args,
      {
        "id": result.id,
        "mode": "purge",
        "id_freed": result.id_freed,
        "reason": result.reason,
        "file_removed": result.file_removed,
      },
      f"{result.id} deleted \u00b7 id {freed}: {result.reason}",
    )
    return OK
  item = ops.retire(state, args.id, reason=args.reason, force=args.force)
  _emit(
    args,
    item.to_dict() | {"mode": "retire"},
    f"{item.id} retired \u00b7 {item.reason}\n"
    f"     id stays claimed; still listed as {state.config.status_label(item.status)}",
  )
  return OK
