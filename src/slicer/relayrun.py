"""The relay run ledger, `<git common dir>/slicer/relay-runs.jsonl`.

A slice relay is an orchestrator driving agents. Nothing in the roadmap state records
which agent or model is working, and per-step telemetry does not belong in the git-tracked
history log. So the orchestrator appends events here, one JSON object per line, and
`relay-run list` (and the TUI board) fold them into the current runs.

In a git repo the ledger lives in the git common dir, so every worktree of the repo shares
one and removing a slice worktree does not delete its runs. Outside git, or for a project
below the worktree root, it is `.slicer/relay-runs.jsonl` as before; that file is also what
older slicers wrote in every worktree, so the readers still look there.

A role whose sandbox may write only inside its slice worktree cannot write the common dir.
Its events go to the checkout's own `.slicer/relay-runs.jsonl` instead (a fallback `ops`
decides on, per write), and every reader folds the shared ledger and those local files into
one set of runs: events are ordered by time and an event seen twice counts once. When a run
ends, `ops` copies its local events into the shared ledger, so removing the worktree loses
nothing.

Like `.slicer/feedback.md` the ledger is untracked and disposable: it is never rendered,
never read by `check`, and never part of roadmap state. Deleting it loses only the board.
Events are appended and never rewritten; a run, its steps and their states exist only as
the fold of those events. Whether a run is `active`, `stale` or `ended` is computed at
read time from a clock the caller passes in, so it is never stored and tests can fix it.

This module reads, folds and appends. Validation of user input and the lock around an
append live in `ops`, which is where every mutation goes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from slicer import feedback, jsonio, store, vcs
from slicer.errors import SlicerError, StateError, reject_future_schema

LEDGER_NAME = "relay-runs.jsonl"
# Under the git common dir, which holds git's own files: a directory of ours keeps them apart.
COMMON_DIR = "slicer"
COMMON_LOCK_NAME = "relay-runs.lock"
VERSION = 1
# A run with no event for longer than this is stale: the orchestrator probably died.
STALE_AFTER = timedelta(minutes=30)
PHASES = ("start", "done", "fail")
OUTCOMES = ("pass", "fail", "stopped")
STATES = ("active", "stale", "ended")
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_FIELDS = ("role", "agent", "vendor", "model", "reasoning", "task")
_REQUIRED = {
  "begin": ("run", "at", "items", "worktree"),
  "step": ("run", "at", "step", "phase") + _FIELDS + ("parent",),
  "end": ("run", "at", "outcome"),
}


def utcnow() -> datetime:
  """The clock. Tests patch this; everything else takes `now` as a parameter."""
  return datetime.now(timezone.utc).replace(microsecond=0)


def stamp(when: datetime) -> str:
  return when.astimezone(timezone.utc).strftime(_STAMP)


def _parse_stamp(text: str) -> datetime:
  return datetime.strptime(text, _STAMP).replace(tzinfo=timezone.utc)


def legacy_ledger_path(root: Path) -> Path:
  """The ledger inside a checkout's own `.slicer/`: the pre-s298 location, still the one
  outside git."""
  return root / store.DIR_NAME / LEDGER_NAME


# A step id is a plain number, which the shared ledger issues, or a checkout's prefix and a
# number, which its own file issues (`fallback_step_prefix`).
_STEP_ID = re.compile(r"c[0-9a-f]{8}-[0-9]+|[0-9]+")


def fallback_step_prefix(root: Path) -> str:
  """The prefix a checkout's own ledger file puts on the step ids it issues: `c`, eight hex
  digits and `-`.

  The hex digits start SHA-256 over the checkout's resolved path, so one checkout gets the same
  prefix on every call. Two checkouts differ with the odds of a 32-bit hash collision. The
  letter keeps an id from being all digits, so no fallback id is a shared-ledger number.
  """
  digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()
  return f"c{digest[:8]}-"


def valid_step_id(text: str) -> bool:
  """Whether a step id may appear in a ledger: a plain number, or a checkout prefix and one."""
  return _STEP_ID.fullmatch(text) is not None


def next_step_id(run: Run, prefix: str = "") -> str:
  """The id for a new step of `run`: one past the largest number among the run's steps whose id
  carries `prefix`. The shared ledger takes no prefix, so its numbering counts only plain numbers
  and skips the prefixed ids a checkout's own file holds.
  """
  taken = []
  for step in run.steps:
    rest = step.id[len(prefix):] if step.id.startswith(prefix) else ""
    if rest.isascii() and rest.isdigit():
      taken.append(int(rest))
  return f"{prefix}{max(taken, default=0) + 1}"


def common_ledger_path(root: Path) -> Path | None:
  """The repo-wide ledger when `root` is a git worktree root, else None.

  A project below the worktree root keeps its own ledger: the other worktrees' ledgers are
  of another project there, and so would a shared one be.
  """
  if not vcs.at_worktree_root(root):
    return None
  common = vcs.git_common_dir(root)
  return None if common is None else common / COMMON_DIR / LEDGER_NAME


def ledger_path(root: Path) -> Path:
  """Where `root` appends and reads its runs."""
  return common_ledger_path(root) or legacy_ledger_path(root)


def bad_field(message: str) -> StateError:
  return StateError(message, code="bad_run_field")


def one_line(name: str, value: str, *, required: bool = False) -> str:
  """A value that fits on one line of the board and in one ledger event."""
  text = value.strip()
  if "\n" in text or "\r" in text:
    raise bad_field(f"{name} must be one line")
  if required and not text:
    raise bad_field(f"{name} may not be blank")
  return text


def choice(name: str, value: str, allowed: tuple[str, ...]) -> str:
  if value not in allowed:
    raise bad_field(f"{name} {value!r} is not one of {', '.join(allowed)}")
  return value


@dataclass
class Step:
  """One agent's turn in a run. `parent` names the step that spawned it, if any."""

  id: str
  role: str
  agent: str
  vendor: str
  model: str
  reasoning: str
  phase: str
  task: str
  parent: str
  begun_at: str
  updated_at: str

  def to_dict(self) -> dict[str, str]:
    return {
      "id": self.id, "role": self.role, "agent": self.agent, "vendor": self.vendor,
      "model": self.model, "reasoning": self.reasoning, "phase": self.phase,
      "task": self.task, "parent": self.parent,
      "begun_at": self.begun_at, "updated_at": self.updated_at,
    }

  @classmethod
  def from_dict(cls, data: dict) -> "Step":
    return cls(
      id=data["id"], role=data["role"], agent=data["agent"], vendor=data["vendor"],
      model=data["model"], reasoning=data["reasoning"], phase=data["phase"],
      task=data["task"], parent=data["parent"],
      begun_at=data["begun_at"], updated_at=data["updated_at"],
    )


@dataclass
class Run:
  """The fold of one run's events. `state` is as of the clock the fold was given."""

  id: str
  items: list[str]
  worktree: str
  begun_at: str
  updated_at: str
  state: str = "active"
  outcome: str = ""
  steps: list[Step] = field(default_factory=list)

  def counts(self) -> dict[str, object]:
    roles: dict[str, int] = {}
    for step in self.steps:
      if not step.parent:  # a subagent is counted under `subagents`, not under its role
        roles[step.role] = roles.get(step.role, 0) + 1
    return {
      "steps": len(self.steps),
      # An ended run runs nothing, whatever phase its last events left its steps in.
      "running": 0 if self.outcome else sum(1 for step in self.steps if step.phase == "start"),
      "subagents": sum(1 for step in self.steps if step.parent),
      "roles": roles,
    }

  def to_dict(self) -> dict[str, object]:
    return {
      "id": self.id, "items": list(self.items), "worktree": self.worktree,
      "begun_at": self.begun_at, "updated_at": self.updated_at,
      "state": self.state, "outcome": self.outcome, "counts": self.counts(),
      "steps": [step.to_dict() for step in self.steps],
    }

  @classmethod
  def from_dict(cls, data: dict) -> "Run":
    # `counts` is derived from the steps, so it is not read back.
    return cls(
      id=data["id"], items=list(data["items"]), worktree=data["worktree"],
      begun_at=data["begun_at"], updated_at=data["updated_at"],
      state=data["state"], outcome=data["outcome"],
      steps=[Step.from_dict(step) for step in data["steps"]],
    )

  def step(self, step_id: str) -> Step | None:
    return next((step for step in self.steps if step.id == step_id), None)

  def subagents_of(self, step_id: str) -> list[Step]:
    """The steps spawned directly by this one, in the order they were added."""
    return [step for step in self.steps if step.parent == step_id]


def begin_event(run: str, at: datetime, items: list[str], worktree: str) -> dict[str, object]:
  return {
    "v": VERSION, "event": "begin", "run": run, "at": stamp(at),
    "items": items, "worktree": worktree,
  }


def step_event(
  run: str, at: datetime, step: str, phase: str, parent: str = "", **fields: str,
) -> dict[str, object]:
  event: dict[str, object] = {
    "v": VERSION, "event": "step", "run": run, "at": stamp(at), "step": step, "phase": phase,
  }
  event.update({name: fields.get(name, "") for name in _FIELDS})
  event["parent"] = parent
  return event


def end_event(run: str, at: datetime, outcome: str) -> dict[str, object]:
  return {"v": VERSION, "event": "end", "run": run, "at": stamp(at), "outcome": outcome}


def _corrupt(path: Path, line: int, message: str) -> StateError:
  return StateError(
    f"{path}:{line}: {message}. The ledger is disposable: fix or delete that line, "
    "or delete the file",
    code="corrupt",
  )


def read_events(path: Path, *, tolerate_torn_tail: bool = False) -> list[tuple[int, dict]]:
  """Each event with its 1-based line number. A missing ledger has no events.

  Lines split on "\n" only: `str.splitlines` would also break on U+2028, U+2029 and U+0085,
  which a task may legitimately contain. With `tolerate_torn_tail`, a last line that has no
  newline yet is skipped as an append in progress; the readers that take no lock use it.
  """
  if not path.is_file():
    return []
  try:
    text = path.read_bytes().decode("utf-8")
  except UnicodeDecodeError as exc:
    raise StateError(f"{path}: not valid UTF-8: {exc}", code="corrupt") from None
  lines = text.split("\n")
  if tolerate_torn_tail:
    lines[-1] = ""  # after a final newline this is already empty
  events: list[tuple[int, dict]] = []
  for number, line in enumerate(lines, 1):
    if not line.strip():
      continue
    try:
      event = json.loads(line)
    except json.JSONDecodeError as exc:
      raise _corrupt(path, number, f"not valid JSON ({exc})") from None
    if not isinstance(event, dict) or not isinstance(event.get("v"), int):
      raise _corrupt(path, number, "not a relay-run event (an object with a version `v`)")
    reject_future_schema(f"{path}:{number}", event["v"], VERSION)
    kind = event.get("event")
    if not isinstance(kind, str) or kind not in _REQUIRED:
      raise _corrupt(path, number, f"unknown event {kind!r}")
    missing = [key for key in _REQUIRED[kind] if key not in event]
    if missing:
      raise _corrupt(path, number, f"{kind} event lacks {', '.join(missing)}")
    for key in _REQUIRED[kind]:
      if key == "items":
        if not isinstance(event[key], list) or not all(isinstance(i, str) for i in event[key]):
          raise _corrupt(path, number, "begin event items is not a list of strings")
      elif not isinstance(event[key], str):
        raise _corrupt(path, number, f"{kind} event {key} is not a string")
    if kind == "step" and not valid_step_id(event["step"]):
      raise _corrupt(
        path, number,
        f"step id {event['step']!r} is neither a number nor a checkout prefix and a number",
      )
    events.append((number, event))
  return events


def event_key(event: dict) -> str:
  """What makes two events the same event: every field, whichever file holds it."""
  return json.dumps(event, sort_keys=True, ensure_ascii=False)


def _rank(event: dict) -> int:
  """Among events at the same second: a begin, then an event that creates a step (it names
  a role and an agent), then the rest. A phase update of a step cannot be overwritten by the
  creation it follows when the two sit in different files."""
  if event["event"] == "begin":
    return 0
  return 1 if event["event"] == "step" and event["role"] and event["agent"] else 2


def valid_event(event: dict) -> bool:
  """Whether the event would fold: a real timestamp and a phase or outcome that exists. Only
  what one event shows; whether its parent step exists is the fold's to say."""
  try:
    _parse_stamp(str(event["at"]))
  except ValueError:
    return False
  if event["event"] == "step":
    return event["phase"] in PHASES
  if event["event"] == "end":
    return event["outcome"] in OUTCOMES
  return True


def fold(
  sources: list[tuple[Path, list[tuple[int, dict]]]], now: datetime, *, strict: int = 1,
) -> list[Run]:
  """The runs the events of these files describe, with `state` as of `now`.

  Events are ordered by time. At the same second the file given first and then the line
  decide; a begin goes before the run's other events, and no event is placed before its own
  run's begin, so a clock that stepped back cannot orphan a step. An event identical to one
  already folded is dropped: a copy of a run's events in a second file changes nothing.

  The first `strict` files must be sound: a bad event in one is `corrupt`, naming file and
  line. A bad event in a later file is skipped, so a sibling's damaged local ledger cannot
  blank the board; that includes events of a run no file begins.
  """
  items: list[tuple[int, Path, int, dict, bool]] = []
  for index, (path, events) in enumerate(sources):
    for number, event in events:
      try:
        _parse_stamp(str(event["at"]))
      except ValueError:
        if index < strict:
          raise _corrupt(path, number, f"bad timestamp {event['at']!r}") from None
        continue
      items.append((index, path, number, event, index < strict))
  began: dict[str, str] = {}
  for _, _, _, event, _ in items:
    if event["event"] == "begin":
      began[event["run"]] = min(event["at"], began.get(event["run"], event["at"]))

  def order(item: tuple[int, Path, int, dict, bool]) -> tuple[str, int, int, int]:
    index, _, number, event, _ = item
    at = event["at"]
    if event["event"] != "begin":
      at = max(at, began.get(event["run"], at))
    return (at, _rank(event), index, number)

  runs: dict[str, Run] = {}
  seen: set[str] = set()
  for _, path, number, event, sound in sorted(items, key=order):
    key = event_key(event)
    if key in seen:
      continue
    try:
      _apply(runs, path, number, event)
    except StateError:
      if sound:
        raise
      continue
    seen.add(key)
  for run in runs.values():
    run.state = state_of(run, now)
  return list(runs.values())


def _apply(runs: dict[str, Run], path: Path, number: int, event: dict) -> None:
  """Fold one event into `runs`; `corrupt` when it cannot be part of them."""
  kind, at = event["event"], event["at"]
  if kind == "begin":
    if event["run"] in runs:
      raise _corrupt(path, number, f"run {event['run']} begins twice")
    runs[event["run"]] = Run(
      id=event["run"], items=[str(item) for item in event["items"]],
      worktree=str(event["worktree"]), begun_at=at, updated_at=at,
    )
    return
  run = runs.get(event["run"])
  if run is None:
    raise _corrupt(path, number, f"{kind} event for run {event['run']}, which never began")
  if kind == "end":
    try:
      choice("outcome", event["outcome"], OUTCOMES)
    except StateError as exc:
      raise _corrupt(path, number, str(exc)) from None
    run.updated_at = at
    run.outcome = event["outcome"]
    return
  try:
    choice("phase", event["phase"], PHASES)
  except StateError as exc:
    raise _corrupt(path, number, str(exc)) from None
  step = run.step(str(event["step"]))
  if step is None:
    parent = str(event["parent"])
    if parent and run.step(parent) is None:
      raise _corrupt(path, number, f"step {event['step']} names parent {parent}, not in the run")
    step = Step(
      id=str(event["step"]), role="", agent="", vendor="", model="", reasoning="",
      phase=event["phase"], task="", parent=parent, begun_at=at, updated_at=at,
    )
    run.steps.append(step)
  run.updated_at = at
  step.updated_at = at
  step.phase = event["phase"]
  for name in _FIELDS:
    if event[name]:  # an update names only what changes
      setattr(step, name, str(event[name]))


def state_of(run: Run, now: datetime) -> str:
  if run.outcome:
    return "ended"
  if now - _parse_stamp(run.updated_at) > STALE_AFTER:
    return "stale"
  return "active"


def local_ledger_paths(root: Path, *, siblings: bool = True) -> list[Path]:
  """The checkout-local ledgers to read besides the shared one: this checkout's, then each
  sibling worktree's. Empty outside the shared-ledger layout, where the local file is the
  ledger."""
  if common_ledger_path(root) is None:
    return []
  checkouts = [root, *(vcs.sibling_worktrees(root) if siblings else [])]
  return [legacy_ledger_path(checkout) for checkout in checkouts]


def _read_local(path: Path, damaged: list[Path] | None = None) -> list[tuple[int, dict]]:
  """A local ledger's events, or none when it cannot be read: another checkout's damaged
  or unreadable file is skipped, not fatal. A skipped file is added to `damaged`."""
  try:
    return read_events(path, tolerate_torn_tail=True)
  except (OSError, SlicerError):
    if damaged is not None:
      damaged.append(path)
    return []


def load_all(
  root: Path, now: datetime | None = None, *, siblings: bool = True,
  tolerate_torn_tail: bool = False,
) -> list[Run]:
  """The runs in every ledger `root` reads, folded together.

  The ledger `root` writes to must be sound (`corrupt` otherwise, with file and line), and
  writers read it strictly, so a torn last line stops them. A reader that holds no lock
  passes `tolerate_torn_tail`, because it may poll while an event is being appended. In a
  git repo the checkout-local ledgers join it: events a sandboxed role wrote there, and those
  an older slicer wrote in every worktree.
  """
  shared = ledger_path(root)
  sources = [(shared, read_events(shared, tolerate_torn_tail=tolerate_torn_tail))]
  sources.extend(
    (path, _read_local(path)) for path in local_ledger_paths(root, siblings=siblings)
  )
  return fold(sources, now or utcnow())


def local_run_events(root: Path, run_id: str) -> tuple[list[dict], list[Path], int]:
  """One run's events from the checkout-local ledgers, this checkout's file first, with its
  begin ahead of the rest; the local files that could not be read; and how many of the run's
  events were left out as not valid (`valid_event`), which the strict shared ledger must
  not be given."""
  damaged: list[Path] = []
  found = [
    event for path in local_ledger_paths(root) for _, event in _read_local(path, damaged)
    if event["run"] == run_id
  ]
  events = [event for event in found if valid_event(event)]
  events.sort(key=lambda event: event["event"] != "begin")
  return events, damaged, len(found) - len(events)


def list_runs(
  root: Path, now: datetime | None = None, *, siblings: bool = True, active: bool = False,
) -> list[Run]:
  """Every run on this machine's checkouts of the project, oldest first.

  The TUI board and `relay-run list` both call this. It reads the ledger `root` writes to,
  which must be readable (`corrupt` otherwise). In a git repo that is the shared one, so the
  runs of every worktree, removed ones included, are already in it. The checkout-local
  `.slicer/relay-runs.jsonl` of this checkout and, with `siblings`, of each sibling worktree
  is folded in as well, for the steps a sandboxed role could only write there and for runs
  an older slicer recorded there. A local file that is not readable is skipped, so another
  worktree's damaged file cannot blank the board. A run in more than one file is one run.
  `active` keeps only runs that are neither ended nor stale.
  """
  runs = load_all(root, now, siblings=siblings, tolerate_torn_tail=True)
  if active:
    runs = [run for run in runs if run.state == "active"]
  return sorted(runs, key=lambda run: (run.begun_at, run.id))


def append(root: Path, event: dict[str, object], *, path: Path | None = None) -> None:
  """Append one event line. The caller holds the ledger lock (`ops.ledger_lock`).

  Without `path` the event goes to the ledger `root` writes to. A `path` is a fallback
  target, the checkout's own file: it never touches the ignore rules, because a write a
  sandbox forced must not put a tracked change into the slice's commit.
  """
  if path is None:
    path = common_ledger_path(root)
    if path is None:
      feedback.ensure_gitignore(root, LEDGER_NAME)  # the ledger sits in the checkout
      path = legacy_ledger_path(root)
  if path.is_file():
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
      # A complete last event that only lacks its newline (a hand edit) stays its own line.
      # A torn one never gets here: every writer reads the ledger strictly before it appends.
      with path.open("ab") as fh:
        fh.write(b"\n")
  jsonio.append_jsonl(path, event)
