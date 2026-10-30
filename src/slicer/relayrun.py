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

A run that waits on the owner (for an approval or an answer) is not stale however long it
waits, so a step can be in the `wait` phase. That phase is the fold's, not the wire's: an
older slicer reading the same shared ledger refuses an event with a phase it does not know as
`corrupt`, which would blank its board and stop its writers. So the event for a wait is a
`start` step event, which every reader folds as a heartbeat, carrying a `waiting_on` field the
older readers ignore (they show the step as running and the run goes stale after 30 minutes,
as it did before). The fold turns that into the phase `wait`. Any later event of the step
without `waiting_on` (a `start`, `done` or `fail`) ends the wait.

A question to the owner (`relay-run ask`) and its answer (`relay-run answer`) ride the same
way, for the same reason. The ask is a `start` step event with `waiting_on` `owner` and a
`question` object; the answer is a step event without `waiting_on`, so it ends the wait, with an
`answer` object naming the question. Both carry every key of any step event, so an older slicer
folds them as heartbeats and ignores the objects. A question lives on the step it was asked on.

`relay-run stats` measures finished runs from those events alone, by the rules `stats` and
`docs/commands.md` state. The fold keeps each step's per-event `history` for it; nothing about
a measurement is stored.

This module reads, folds and appends. Validation of user input and the lock around an
append live in `ops`, which is where every mutation goes.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path

from slicer import feedback, jsonio, roles, store, vcs
from slicer.errors import SlicerError, StateError, reject_future_schema

LEDGER_NAME = "relay-runs.jsonl"
# Under the git common dir, which holds git's own files: a directory of ours keeps them apart.
COMMON_DIR = "slicer"
COMMON_LOCK_NAME = "relay-runs.lock"
VERSION = 1
# A run with no event for longer than this is stale: the orchestrator probably died.
STALE_AFTER = timedelta(minutes=30)
# The phases an event carries on the wire, and so the phases every slicer can fold.
PHASES = ("start", "done", "fail")
# What `relay-run step --phase` takes: `wait` is recorded as a `start` event with `waiting_on`.
STEP_PHASES = PHASES + ("wait",)
# What a step may wait on. The fold takes any non-empty string, so a newer slicer's value
# still counts as a wait here.
WAITING_ON = ("owner", "approval")
DEFAULT_WAITING_ON = "owner"
# An owner question has 2 to 4 options, as the run report's `owner needed` line does.
OPTIONS_MIN = 2
OPTIONS_MAX = 4
OUTCOMES = ("pass", "fail", "stopped")
# The verdict a gate round is closed with, as the gate's `VERDICT:` line says it.
VERDICTS = ("pass", "fail")
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


def elapsed_text(start: str, end: datetime) -> str:
  """How long from the stamp `start` to `end`, as `5s`, `1m05s` or `1h02m`, never negative.
  The board and `relay-run list` both show it, so it lives here, not in `tui`."""
  return duration_text(int((end - _parse_stamp(start)).total_seconds()))


def duration_text(seconds: int) -> str:
  """A length of time as `5s`, `1m05s` or `1h02m`, never negative."""
  seconds = max(0, seconds)
  if seconds < 60:
    return f"{seconds}s"
  if seconds < 3600:
    return f"{seconds // 60}m{seconds % 60:02d}s"
  return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def legacy_ledger_path(root: Path) -> Path:
  """The ledger inside a checkout's own `.slicer/`: the pre-s298 location, still the one
  outside git."""
  return root / store.DIR_NAME / LEDGER_NAME


# A step id is a plain number, which the shared ledger issues, or a checkout's prefix and a
# number, which its own file issues (`fallback_step_prefix`).
_STEP_ID = re.compile(r"c[0-9a-f]{8}-[0-9]+|[0-9]+")
# A question id is `q` and a number, which the shared ledger issues, or a checkout's prefix first,
# which its own file issues, as for a step.
_QUESTION_ID = re.compile(r"(?P<prefix>c[0-9a-f]{8}-)?q(?P<number>[0-9]+)")


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


def next_question_id(run: Run, prefix: str = "") -> str:
  """The id for a new question of `run`: `q` and one past the largest number among the run's
  questions that carry `prefix`, like `next_step_id`."""
  taken = []
  for step in run.steps:
    for question in step.questions:
      found = _QUESTION_ID.fullmatch(question.id)
      if found and (found["prefix"] or "") == prefix:
        taken.append(int(found["number"]))
  return f"{prefix}q{max(taken, default=0) + 1}"


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


# The separators of the run report's `owner needed` line. `question_fields` refuses a value that
# would break the line, and the Run report states the same rule through QUESTION_VALUE_RULE.
FIELD_SEPARATOR = "|"
OPTION_SEPARATOR = ";"
LABEL_SEPARATOR = ":"
QUESTION_VALUE_RULE = (
  f"Use no `{FIELD_SEPARATOR}` in any value but `reasoning`, no `{OPTION_SEPARATOR}` in a label "
  f"or summary, and the first `{LABEL_SEPARATOR}` ends the label."
)


def _plain(name: str, value: str, *, required: bool = False, spaces: bool = True) -> str:
  """A question value that sits in a `|`-separated report line: one line, no `|`."""
  text = one_line(name, value, required=required)
  if FIELD_SEPARATOR in text:
    raise bad_field(
      f"{name} may not contain `{FIELD_SEPARATOR}`, which separates the fields of the report line"
    )
  if not spaces and re.search(r"\s", text):
    raise bad_field(f"{name} may not contain whitespace")
  return text


def question_fields(
  title: str, options: list[str], recommend: str = "", key: str = "", reasoning: str = "",
) -> tuple[str, list[dict[str, str]], str, str, str]:
  """The checked fields of an owner question, in the shape of the run report's `owner needed`
  line: a title, 2 to 4 `LABEL:SUMMARY` options with distinct labels, the label of the preferred
  one, an `AREA.TOPIC` key and the reasoning. No value but the reasoning holds a `|`, and no label
  or summary holds a `;`, so the report line parses back; the label ends at the first `:`."""
  title = _plain("title", title, required=True)
  if not OPTIONS_MIN <= len(options) <= OPTIONS_MAX:
    raise bad_field(
      f"a question takes {OPTIONS_MIN} to {OPTIONS_MAX} --option LABEL:SUMMARY, not {len(options)}"
    )
  parsed: list[dict[str, str]] = []
  for raw in options:
    label, colon, summary = raw.partition(LABEL_SEPARATOR)
    if not colon:
      raise bad_field(f"option {raw!r} is not LABEL{LABEL_SEPARATOR}SUMMARY")
    label = _plain("option label", label, required=True)
    summary = _plain("option summary", summary, required=True)
    if OPTION_SEPARATOR in label or OPTION_SEPARATOR in summary:
      raise bad_field(
        f"option {raw!r} may not contain `{OPTION_SEPARATOR}`, which separates the options"
      )
    if any(option["label"] == label for option in parsed):
      raise bad_field(f"option label {label!r} is used twice")
    parsed.append({"label": label, "summary": summary})
  recommend = _plain("recommend", recommend)
  if recommend and recommend not in [option["label"] for option in parsed]:
    raise bad_field(
      f"--recommend {recommend!r} is not an option label: "
      f"{', '.join(option['label'] for option in parsed)}"
    )
  key = _plain("key", key, spaces=False)
  return title, parsed, recommend, key, one_line("reasoning", reasoning)


@dataclass
class Question:
  """An owner question asked on a step, and its answer once there is one.

  `asked_at` and `answered_at` are the stamps of the events that wrote them; `answered_at` is
  `""` and `answer` is None while the question is open. The answer is `{option, text, why, by}`:
  `option` names one of the options and `text` is `""`, or `text` is free text and `option` is
  `""`.
  """

  id: str
  title: str
  options: list[dict[str, str]]
  recommend: str
  blocker: bool
  key: str
  reasoning: str
  asked_at: str
  answered_at: str = ""
  answer: dict[str, str] | None = None

  def to_dict(self) -> dict[str, object]:
    return {
      "id": self.id, "title": self.title, "options": [dict(o) for o in self.options],
      "recommend": self.recommend, "blocker": self.blocker, "key": self.key,
      "reasoning": self.reasoning, "asked_at": self.asked_at,
      "answered_at": self.answered_at,
      "answer": None if self.answer is None else dict(self.answer),
    }

  @classmethod
  def from_dict(cls, data: dict) -> "Question":
    return cls(
      id=data["id"], title=data["title"], options=[dict(o) for o in data["options"]],
      recommend=data["recommend"], blocker=data["blocker"], key=data["key"],
      reasoning=data["reasoning"], asked_at=data["asked_at"],
      answered_at=data["answered_at"],
      answer=None if data["answer"] is None else dict(data["answer"]),
    )

  @classmethod
  def from_event(cls, data: dict, at: str) -> "Question":
    """The question a `question` object on an event asks. Only what `_is_question` checks is
    required; a field a newer slicer adds is ignored, one it lacks is empty."""
    return cls(
      id=str(data.get("id", "")), title=str(data.get("title", "")),
      options=[
        {"label": str(o.get("label", "")), "summary": str(o.get("summary", ""))}
        for o in data.get("options") or [] if isinstance(o, dict)
      ],
      recommend=str(data.get("recommend", "")), blocker=bool(data.get("blocker", False)),
      key=str(data.get("key", "")), reasoning=str(data.get("reasoning", "")), asked_at=at,
    )


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
  # What the step waits on while its phase is `wait`, else "".
  waiting_on: str = ""
  # The stamp of the event that began the current wait, else "". A repeat of the same wait keeps
  # it; a `start`, `done` or `fail` clears it.
  waiting_since: str = ""
  # The questions asked on the step, oldest first. `question` is the latest.
  questions: list[Question] = field(default_factory=list)
  # Why the step failed, when its latest event is a `fail` that gave a reason, else "".
  reason: str = ""
  # The verdict a gate round was closed with, when the latest event is a `done` that gave one.
  verdict: str = ""
  # Every event the fold put into the step, as `(at, folded phase, waiting_on)`, oldest first.
  # Not serialized: `stats` needs the intervals the latest-phase fold drops.
  history: list[tuple[str, str, str]] = field(default_factory=list)

  @property
  def waiting(self) -> bool:
    """Whether the step is waiting on someone else, so its run is not stale.

    The one place that says so: a later kind of wait joins here and every reader follows. An
    open question to the owner is already one, since `relay-run ask` writes the wait with it.
    """
    return self.phase == "wait"

  @property
  def question(self) -> Question | None:
    """The latest question asked on the step, open or answered."""
    return self.questions[-1] if self.questions else None

  def to_dict(self) -> dict[str, object]:
    question = self.question
    return {
      "id": self.id, "role": self.role, "agent": self.agent, "vendor": self.vendor,
      "model": self.model, "reasoning": self.reasoning, "phase": self.phase,
      "waiting_on": self.waiting_on, "waiting_since": self.waiting_since,
      "task": self.task, "parent": self.parent,
      "begun_at": self.begun_at, "updated_at": self.updated_at,
      "question": None if question is None else question.to_dict(),
      "reason": self.reason, "verdict": self.verdict,
    }

  @classmethod
  def from_dict(cls, data: dict) -> "Step":
    return cls(
      id=data["id"], role=data["role"], agent=data["agent"], vendor=data["vendor"],
      model=data["model"], reasoning=data["reasoning"], phase=data["phase"],
      task=data["task"], parent=data["parent"],
      begun_at=data["begun_at"], updated_at=data["updated_at"],
      waiting_on=data.get("waiting_on", ""), waiting_since=data.get("waiting_since", ""),
      reason=data.get("reason", ""), verdict=data.get("verdict", ""),
      questions=[Question.from_dict(data["question"])] if data.get("question") else [],
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
  # The stamp of the end event, else "". Not serialized: `stats` reads it.
  ended_at: str = ""

  def in_lane(self, step: Step) -> bool:
    """Whether the step is drawn in the lane: nothing spawned it, or the orchestrator did. A step
    that any other role spawned is that role's subagent, counted under its parent and not a lane
    step of its own. The board and `counts.roles` both use this rule."""
    if not step.parent:
      return True
    parent = self.step(step.parent)
    return parent is not None and parent.role == "orchestrator"

  def counts(self) -> dict[str, object]:
    roles: dict[str, int] = {}
    for step in self.steps:
      if self.in_lane(step):  # a subagent is counted under `subagents`, not under its role
        roles[step.role] = roles.get(step.role, 0) + 1
    return {
      "steps": len(self.steps),
      # An ended run runs nothing, whatever phase its last events left its steps in.
      "running": 0 if self.outcome else sum(1 for step in self.steps if step.phase == "start"),
      "waiting": 0 if self.outcome else sum(1 for step in self.steps if step.waiting),
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

  @property
  def waiting(self) -> bool:
    """Whether a step of the run waits on someone else and the run has not ended."""
    return not self.outcome and any(step.waiting for step in self.steps)

  def unfinished_steps(self) -> list[Step]:
    """The steps neither done nor failed: the ones running and the ones waiting."""
    return [step for step in self.steps if step.phase in ("start", "wait")]

  def step(self, step_id: str) -> Step | None:
    return next((step for step in self.steps if step.id == step_id), None)

  def find_question(self, question_id: str) -> tuple[Step, Question] | None:
    """The step and the question with this id, whichever step it was asked on."""
    for step in self.steps:
      for question in step.questions:
        if question.id == question_id:
          return step, question
    return None

  def subagents_of(self, step_id: str) -> list[Step]:
    """The steps spawned directly by this one, in the order they were added."""
    return [step for step in self.steps if step.parent == step_id]


def begin_event(run: str, at: datetime, items: list[str], worktree: str) -> dict[str, object]:
  return {
    "v": VERSION, "event": "begin", "run": run, "at": stamp(at),
    "items": items, "worktree": worktree,
  }


def question_payload(
  question_id: str, title: str, options: list[dict[str, str]], recommend: str, blocker: bool,
  key: str, reasoning: str,
) -> dict[str, object]:
  """The `question` object of an ask event, keys in the order of the run report's line."""
  return {
    "id": question_id, "title": title, "options": options, "recommend": recommend,
    "blocker": blocker, "key": key, "reasoning": reasoning,
  }


def answer_payload(question_id: str, option: str, text: str, why: str, by: str) -> dict[str, object]:
  """The `answer` object of an answer event."""
  return {"question": question_id, "option": option, "text": text, "why": why, "by": by}


def step_event(
  run: str, at: datetime, step: str, phase: str, parent: str = "", waiting_on: str = "",
  question: dict[str, object] | None = None, answer: dict[str, object] | None = None,
  reason: str = "", verdict: str = "", **fields: str,
) -> dict[str, object]:
  """A step event. `waiting_on` marks a `start` event as a wait, `question` says what it asks
  and `answer` what it answers, `reason` says why a `fail` event failed and `verdict` closes a
  gate round; each is left out when not given, so every other event is the line an older
  slicer wrote."""
  event: dict[str, object] = {
    "v": VERSION, "event": "step", "run": run, "at": stamp(at), "step": step, "phase": phase,
  }
  event.update({name: fields.get(name, "") for name in _FIELDS})
  event["parent"] = parent
  if waiting_on:
    event["waiting_on"] = waiting_on
  if question is not None:
    event["question"] = question
  if answer is not None:
    event["answer"] = answer
  if reason:
    event["reason"] = reason
  if verdict:
    event["verdict"] = verdict
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
    if kind == "step" and not isinstance(event.get("waiting_on", ""), str):
      raise _corrupt(path, number, "step event waiting_on is not a string")
    if kind == "step" and not isinstance(event.get("reason", ""), str):
      raise _corrupt(path, number, "step event reason is not a string")
    if kind == "step" and not isinstance(event.get("verdict", ""), str):
      raise _corrupt(path, number, "step event verdict is not a string")
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

  Events are ordered by time, then rank (begin, step creation, other events). Within a
  time and rank, file order then line decide, pulling a step's parent creation and its
  ancestors ahead of it when needed. No event is placed before its own run's begin, so a
  clock that stepped back cannot orphan a step. An event identical to one
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

  ordered = []
  for (_, rank), tied in groupby(sorted(items, key=order), key=lambda item: order(item)[:2]):
    group = list(tied)
    if rank != 1:
      ordered.extend(group)
      continue
    creations: dict[tuple[str, str], int] = {}
    for position, (_, _, _, event, _) in enumerate(group):
      creations.setdefault((event["run"], event["step"]), position)
    placed: set[int] = set()
    for position in range(len(group)):
      chain = []
      while position not in placed:
        placed.add(position)  # also stops cycles; _apply still rejects missing parents
        chain.append(group[position])
        event = group[position][3]
        parent = creations.get((event["run"], event["parent"]))
        if parent is None:
          break
        position = parent
      ordered.extend(reversed(chain))

  runs: dict[str, Run] = {}
  seen: set[str] = set()
  for _, path, number, event, sound in ordered:
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
    run.ended_at = at
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
  before = (step.phase, step.waiting_on)
  step.phase = event["phase"]
  step.waiting_on = ""
  step.reason = str(event["reason"]) if event["phase"] == "fail" and event.get("reason") else ""
  # Only a `done` event closes a gate round with a verdict; a later event of the step ends it.
  closing = event["phase"] == "done" and event.get("verdict") in VERDICTS
  step.verdict = event["verdict"] if closing else ""
  if event["phase"] == "start" and event.get("waiting_on"):
    step.phase = "wait"
    step.waiting_on = str(event["waiting_on"])
  # A wait takes the stamp of the event that began it: a repeat of the same wait keeps that
  # stamp, a change of what it waits on starts a new one, and an event that is not a wait ends it.
  if not step.waiting:
    step.waiting_since = ""
  elif (step.phase, step.waiting_on) != before:
    step.waiting_since = at
  step.history.append((at, step.phase, step.waiting_on))
  for name in _FIELDS:
    if event[name]:  # an update names only what changes
      setattr(step, name, str(event[name]))
  # A `question` or `answer` that is malformed is skipped, not refused: the event is still a
  # heartbeat, and one odd line must not blank the board of every slicer that reads the ledger.
  if _is_question(event.get("question")):
    step.questions.append(Question.from_event(event["question"], at))
  if _is_answer(event.get("answer")):
    _answer(run, event["answer"], at)


def _is_question(value: object) -> bool:
  """Whether a `question` object has what a reader needs: a string id, a string title and a
  list of options."""
  return (
    isinstance(value, dict) and isinstance(value.get("id"), str)
    and isinstance(value.get("title"), str) and isinstance(value.get("options"), list)
  )


def _is_answer(value: object) -> bool:
  return isinstance(value, dict) and isinstance(value.get("question"), str)


def _answer(run: Run, answer: dict, at: str) -> None:
  """Record an answer on its question. One that names no question of the run, or one already
  answered, changes nothing: the first answer stands, and an answer the fold cannot place is
  not worth refusing a ledger that every other slicer reads."""
  found = run.find_question(str(answer["question"]))
  if found is None or found[1].answer is not None:
    return
  question = found[1]
  question.answered_at = at
  question.answer = {
    name: str(answer.get(name, "")) for name in ("option", "text", "why", "by")
  }


def state_of(run: Run, now: datetime) -> str:
  if run.outcome:
    return "ended"
  if run.waiting:
    return "active"  # it waits on someone else, however long that takes
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


# The rounds a lane is counted in: the roles that implement, review and gate a slice.
_ROUND_ROLES = ("implementer", "reviewer", "gate")


def _epoch(text: str) -> int:
  return int(_parse_stamp(text).timestamp())


def _median(values: list[int]) -> int | None:
  """The lower median, so it is a number the ledger observed; None for no values."""
  return statistics.median_low(values) if values else None


def _union_seconds(spans: list[tuple[int, int]]) -> int:
  """The seconds covered by the spans together: overlaps count once."""
  total, reach = 0, None
  for begin, stop in sorted(spans):
    if reach is None or begin > reach:
      total += stop - begin
      reach = stop
    elif stop > reach:
      total += stop - reach
      reach = stop
  return total


def _ordered_roles(present: set[str]) -> list[str]:
  """The roles the ledger names, in `LEDGER_ROLE_NAMES` order, then any other role sorted."""
  known = [name for name in roles.LEDGER_ROLE_NAMES if name in present]
  return known + sorted(present - set(roles.LEDGER_ROLE_NAMES))


def _spans(run: Run, step: Step) -> list[tuple[str, str, int, int]]:
  """`(phase, waiting_on, from, to)` in seconds for each event of the step: an interval runs
  to the next event of the step, and the last one to the end of the run."""
  end = _epoch(run.ended_at) if run.ended_at else None
  spans = []
  for index, (at, phase, waiting_on) in enumerate(step.history):
    begin = _epoch(at)
    if index + 1 < len(step.history):
      stop = _epoch(step.history[index + 1][0])
    else:
      stop = begin if end is None else end
    spans.append((phase, waiting_on, begin, max(begin, stop)))
  return spans


def _measure(run: Run) -> dict[str, object]:
  """One ended run's numbers, by the rules in `docs/commands.md`. Round order is the order
  of the run's steps, which is creation order, because rounds often share a second."""
  lane = [step for step in run.steps if run.in_lane(step)]
  rounds = [step for step in lane if step.role in _ROUND_ROLES]
  fix: dict[str, bool] = {}
  implementer_rounds = fix_rounds = 0
  previous: Step | None = None
  for index, step in enumerate(rounds):
    if step.role != "implementer":
      continue
    if previous is not None and previous.phase == "fail" and previous.reason:
      fix[step.id] = fix[previous.id]  # a rerun after a vendor limit is the same round
    else:
      implementer_rounds += 1
      before = any(other.role != "implementer" for other in rounds[:index])
      after = any(other.role != "implementer" for other in rounds[index + 1:])
      fix[step.id] = before and after
      fix_rounds += fix[step.id]
    previous = step
  gate_rounds = gate_fails = with_verdict = 0
  for index, step in enumerate(rounds):
    if step.role != "gate":
      continue
    gate_rounds += 1
    following = rounds[index + 1] if index + 1 < len(rounds) else None
    sent_back = following is not None and following.role == "implementer" and fix[following.id]
    if step.verdict:
      with_verdict += 1
      gate_fails += step.verdict == "fail"
    else:
      gate_fails += sent_back
  role_seconds: dict[str, list[int]] = {}
  for step in lane:
    seconds = sum(stop - begin for phase, _, begin, stop in _spans(run, step) if phase == "start")
    role_seconds.setdefault(step.role, []).append(seconds)
  waits: dict[str, list[tuple[int, int]]] = {}
  for step in run.steps:
    for phase, waiting_on, begin, stop in _spans(run, step):
      if phase == "wait":
        waits.setdefault(waiting_on, []).append((begin, stop))
  everything = [span for spans in waits.values() for span in spans]
  return {
    "implementer_rounds": implementer_rounds, "fix_rounds": fix_rounds,
    "gate_rounds": gate_rounds, "gate_fails": gate_fails, "with_verdict": with_verdict,
    "wall_seconds": max(0, _epoch(run.ended_at) - _epoch(run.begun_at)),
    "role_seconds": role_seconds,
    "has_wait": bool(everything), "wait_seconds": _union_seconds(everything),
    "wait_by": {kind: _union_seconds(spans) for kind, spans in waits.items()},
  }


def stats(runs: list[Run], since: datetime | None) -> dict[str, object]:
  """The measurements `relay-run stats` prints, from the ended runs that began at or after
  `since` (all of them when None). Pure: no clock, so a ledger always gives the same bytes.

  A run is measured when it has ended and has an implementer step in its lane (`Run.in_lane`).
  Every rule is derived from ledger events alone and stated in `docs/commands.md`: no round
  kind is recorded, so a fix round is an implementer round between a review or gate and a later
  one; a gate verdict is recorded only since s408, so older gates count as failed when a fix
  round follows them; waits are recorded only since s404.
  """
  kept = [run for run in runs if since is None or _parse_stamp(run.begun_at) >= since]
  ended = [run for run in kept if run.outcome]
  rows = [(run, _measure(run)) for run in ended]
  measured = [
    (run, got) for run, got in rows
    if any(step.role == "implementer" and run.in_lane(step) for step in run.steps)
  ]
  walls = [int(got["wall_seconds"]) for _, got in measured]
  fixes = [int(got["fix_rounds"]) for _, got in measured]
  by_role: dict[str, list[int]] = {}
  for _, got in measured:
    for role, seconds in got["role_seconds"].items():
      by_role.setdefault(role, []).extend(seconds)
  waited = [got for _, got in measured if got["has_wait"]]
  kinds = {kind for _, got in measured for kind in got["wait_by"]}
  by_waiting_on = {
    kind: sum(got["wait_by"].get(kind, 0) for _, got in measured)
    for kind in sorted(WAITING_ON) + sorted(kinds - set(WAITING_ON))
  }
  waits = [int(got["wait_seconds"]) for got in waited]
  return {
    "since": None if since is None else stamp(since),
    "runs": {
      "total": len(kept),
      **{outcome: sum(1 for run in ended if run.outcome == outcome) for outcome in OUTCOMES},
      "unended": len(kept) - len(ended),
    },
    "measured": len(measured),
    "wall_seconds": {"median": _median(walls), "max": max(walls, default=None)},
    "fix_rounds": {
      "total": sum(fixes), "runs_with_any": sum(1 for n in fixes if n >= 1),
      "runs_with_two_or_more": sum(1 for n in fixes if n >= 2),
    },
    "gates": {
      "rounds": sum(int(got["gate_rounds"]) for _, got in measured),
      "fails": sum(int(got["gate_fails"]) for _, got in measured),
      "with_verdict": sum(int(got["with_verdict"]) for _, got in measured),
      "by_proxy": sum(int(got["gate_rounds"]) - int(got["with_verdict"]) for _, got in measured),
    },
    "roles": {
      role: {
        "steps": len(by_role[role]), "total_seconds": sum(by_role[role]),
        "median_seconds": _median(by_role[role]),
      }
      for role in _ordered_roles(set(by_role))
    },
    "owner_wait": {
      "runs_with_wait": len(waited), "total_seconds": sum(waits),
      "median_seconds": _median(waits), "max_seconds": max(waits, default=None),
      "by_waiting_on": by_waiting_on,
    },
    "per_run": [
      {
        "id": run.id, "items": list(run.items), "worktree": run.worktree, "outcome": run.outcome,
        "begun_at": run.begun_at, "ended_at": run.ended_at, "wall_seconds": got["wall_seconds"],
        "implementer_rounds": got["implementer_rounds"], "fix_rounds": got["fix_rounds"],
        "gate_rounds": got["gate_rounds"], "gate_fails": got["gate_fails"],
        "wait_seconds": got["wait_seconds"],
        "role_seconds": {
          role: sum(got["role_seconds"][role]) for role in _ordered_roles(set(got["role_seconds"]))
        },
      }
      for run, got in rows
    ],
  }
