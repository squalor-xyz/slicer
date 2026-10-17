"""The repo paths a section body names, so two items can be compared by file.

A slice's Files section lists the paths it will touch in backticks. Which section
that is belongs to the project (`files_section`), so this module only reads text:
a body in, an ordered list of paths out. No I/O, and no check that a path exists.
"""

from __future__ import annotations

import re

_SPAN = re.compile(r"`([^`\n]+)`")
# A path is made of name characters and slashes. Anything with a space, a quote
# or a bracket is code or prose, not a path.
_TOKEN = re.compile(r"[\w.@+\-/]+")
# A `:12` or `:12-20` after the name points at lines, not at another file.
_LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")
# A file extension: a dot and a letter-led name at the end of the last component.
_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]*$")


def extract(body: str) -> list[str]:
  """Backticked paths in `body`, first appearance first, each once.

  A path is a token with a `/` or a file extension. A `:line` suffix is dropped
  and a leading `./` is not part of the name.
  """
  found: list[str] = []
  for span in _SPAN.findall(body):
    token = _LINE_SUFFIX.sub("", span.strip())
    if not _TOKEN.fullmatch(token):
      continue
    if token.startswith("./"):
      token = token[2:]
    if not token.strip("/") or not ("/" in token or _EXTENSION.search(token)):
      continue
    if token not in found:
      found.append(token)
  return found
