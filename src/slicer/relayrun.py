"""The relay run ledger, `.slicer/relay-runs.jsonl`.

A slice relay is an orchestrator driving agents. Nothing in the roadmap state records
which agent or model is working, and per-step telemetry does not belong in the git-tracked
history log. So the orchestrator appends events here, one JSON object per line, and
`relay-run list` (and the TUI board) fold them into the current runs.

Like `.slicer/feedback.md` the ledger is gitignored and disposable: it is never rendered,
never read by `check`, and never part of roadmap state. Deleting it loses only the board.
Events are appended and never rewritten; a run, its steps and their states exist only as
the fold of those events. Whether a run is `active`, `stale` or `ended` is computed at
read time from a clock the caller passes in, so it is never stored and tests can fix it.

This module reads, folds and appends. Validation of user input and the lock around an
append live in `ops`, which is where every mutation goes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from slicer import feedback, jsonio, store, vcs
from slicer.errors import SlicerError, StateError, reject_future_schema

LEDGER_NAME = "relay-runs.jsonl"
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


def ledger_path(root: Path) -> Path:
  return root / store.DIR_NAME / LEDGER_NAME


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
    if kind == "step" and not (event["step"].isascii() and event["step"].isdigit()):
      raise _corrupt(path, number, f"step id {event['step']!r} is not a number")
    events.append((number, event))
  return events


def fold(path: Path, events: list[tuple[int, dict]], now: datetime) -> list[Run]:
  """The runs those events describe, in ledger order, with `state` as of `now`."""
  runs: dict[str, Run] = {}
  for number, event in events:
    try:
      _parse_stamp(str(event["at"]))
    except ValueError:
      raise _corrupt(path, number, f"bad timestamp {event['at']!r}") from None
    kind, at = event["event"], event["at"]
    if kind == "begin":
      if event["run"] in runs:
        raise _corrupt(path, number, f"run {event['run']} begins twice")
      runs[event["run"]] = Run(
        id=event["run"], items=[str(item) for item in event["items"]],
        worktree=str(event["worktree"]), begun_at=at, updated_at=at,
      )
      continue
    run = runs.get(event["run"])
    if run is None:
      raise _corrupt(path, number, f"{kind} event for run {event['run']}, which never began")
    run.updated_at = at
    if kind == "end":
      try:
        choice("outcome", event["outcome"], OUTCOMES)
      except StateError as exc:
        raise _corrupt(path, number, str(exc)) from None
      run.outcome = event["outcome"]
      continue
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
    step.updated_at = at
    step.phase = event["phase"]
    for name in _FIELDS:
      if event[name]:  # an update names only what changes
        setattr(step, name, str(event[name]))
  for run in runs.values():
    run.state = state_of(run, now)
  return list(runs.values())


def state_of(run: Run, now: datetime) -> str:
  if run.outcome:
    return "ended"
  if now - _parse_stamp(run.updated_at) > STALE_AFTER:
    return "stale"
  return "active"


def load_runs(
  root: Path, now: datetime | None = None, *, tolerate_torn_tail: bool = False,
) -> list[Run]:
  """The runs in this checkout's own ledger. A damaged ledger is `corrupt`, with file and line.

  Writers read it strictly, so a torn last line stops them. A reader that holds no lock
  passes `tolerate_torn_tail`, because it may poll while an event is being appended.
  """
  path = ledger_path(root)
  return fold(path, read_events(path, tolerate_torn_tail=tolerate_torn_tail), now or utcnow())


def list_runs(
  root: Path, now: datetime | None = None, *, siblings: bool = True, active: bool = False,
) -> list[Run]:
  """Every run on this machine's checkouts of the project, oldest first.

  The TUI board and `relay-run list` both call this. It reads this checkout's ledger and,
  with `siblings`, each sibling worktree's ledger as a plain file under that worktree's
  root. This checkout's ledger must be readable (`corrupt` otherwise); a sibling's that is
  not is skipped, so another worktree's damaged file cannot blank the board. `active`
  keeps only runs that are neither ended nor stale.
  """
  now = now or utcnow()
  found = load_runs(root, now, tolerate_torn_tail=True)
  if siblings and vcs.at_worktree_root(root):
    for sibling in vcs.sibling_worktrees(root):
      try:
        found.extend(load_runs(sibling, now, tolerate_torn_tail=True))
      except (OSError, SlicerError):
        continue
  if active:
    found = [run for run in found if run.state == "active"]
  return sorted(found, key=lambda run: (run.begun_at, run.id))


def append(root: Path, event: dict[str, object]) -> None:
  """Append one event line. The caller holds the project lock."""
  feedback.ensure_gitignore(root, LEDGER_NAME)
  path = ledger_path(root)
  if path.is_file():
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
      # A complete last event that only lacks its newline (a hand edit) stays its own line.
      # A torn one never gets here: every writer reads the ledger strictly before it appends.
      with path.open("ab") as fh:
        fh.write(b"\n")
  jsonio.append_jsonl(path, event)
