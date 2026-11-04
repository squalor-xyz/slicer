"""Durability of `jsonio.write_bytes`: the data reaches disk before the rename, and the rename after.

`os.replace` makes a write atomic against errors but not against power loss; without
an fsync of the temporary file before the rename (and F_FULLFSYNC as well on macOS) and
of the directory after it, a crash can keep the rename and lose the data (s313).
"""

from __future__ import annotations

import errno
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support  # noqa: F401 - puts src/ on sys.path
from slicer import jsonio


CONTENT = b'{"items": []}\n'


def _dir_fsync_failing_with(code: int, dir_fsyncs: list[int]):
  """An `os.fsync` that fails with `code` on a directory and passes every other descriptor through.

  Each directory fsync is appended to `dir_fsyncs`, so a test can tell that the call was made.
  """
  real_fsync = os.fsync

  def fsync(fd: int) -> None:
    if stat.S_ISDIR(os.fstat(fd).st_mode):
      dir_fsyncs.append(fd)
      raise OSError(code, os.strerror(code))
    real_fsync(fd)

  return fsync


class WriteBytesDurabilityTests(unittest.TestCase):
  def write_recording_calls(self) -> list[tuple[str, str]]:
    """Run `jsonio.write_bytes` and return its fsync and replace calls, in order.

    An fsync is recorded as ("fsync", "dir") when its descriptor is a directory and
    ("fsync", "file") otherwise; a replace as ("replace", <target file name>).
    """
    events: list[tuple[str, str]] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
      events.append(("fsync", "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"))
      real_fsync(fd)

    def replace(src, dst, *args, **kwargs) -> None:
      events.append(("replace", os.path.basename(os.fspath(dst))))
      real_replace(src, dst, *args, **kwargs)

    with tempfile.TemporaryDirectory() as tmp:
      target = Path(tmp) / "index.json"
      with patch("os.fsync", fsync), patch("os.replace", replace):
        jsonio.write_bytes(target, CONTENT)
      self.assertEqual(target.read_bytes(), CONTENT)
    return events

  def test_WriteBytes_FsyncsFileBeforeReplace(self) -> None:
    events = self.write_recording_calls()
    self.assertIn(("fsync", "file"), events)
    self.assertLess(events.index(("fsync", "file")), events.index(("replace", "index.json")))

  def test_WriteBytes_FsyncsDirectoryAfterReplace(self) -> None:
    if os.name == "nt":  # pragma: no cover - POSIX only: a directory cannot be opened for fsync on Windows
      self.skipTest("a directory cannot be opened for fsync on Windows")
    events = self.write_recording_calls()
    self.assertIn(("fsync", "dir"), events)
    self.assertGreater(events.index(("fsync", "dir")), events.index(("replace", "index.json")))

  def test_WriteBytes_FlushesDataBeforeFsync(self) -> None:
    """The bytes must be in the file when it is fsynced; an fsync of a buffered write persists nothing."""
    sizes_at_fsync: list[int] = []
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
      if not stat.S_ISDIR(os.fstat(fd).st_mode):
        sizes_at_fsync.append(os.fstat(fd).st_size)
      real_fsync(fd)

    with tempfile.TemporaryDirectory() as tmp:
      with patch("os.fsync", fsync):
        jsonio.write_bytes(Path(tmp) / "index.json", CONTENT)
    self.assertEqual(sizes_at_fsync, [len(CONTENT)])

  def write_recording_fullfsync(self, platform: str) -> list[tuple[str, str]]:
    """Run `jsonio.write_bytes` as if on `platform`; return its fsync, F_FULLFSYNC and replace calls, in order.

    An fsync and an F_FULLFSYNC are recorded as ("fsync" or "fullfsync", "dir" or "file") by
    their descriptor, and a replace as in `write_recording_calls`. Any other fcntl command passes through.
    """
    import fcntl  # POSIX only; callers skip on Windows

    events: list[tuple[str, str]] = []
    real_fsync, real_fcntl, real_replace = os.fsync, fcntl.fcntl, os.replace

    def fsync(fd: int) -> None:
      events.append(("fsync", "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"))
      real_fsync(fd)

    def fcntl_call(fd: int, cmd: int, *args):
      if cmd == fcntl.F_FULLFSYNC:
        events.append(("fullfsync", "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"))
        return 0
      return real_fcntl(fd, cmd, *args)

    def replace(src, dst, *args, **kwargs) -> None:
      events.append(("replace", os.path.basename(os.fspath(dst))))
      real_replace(src, dst, *args, **kwargs)

    with tempfile.TemporaryDirectory() as tmp:
      target = Path(tmp) / "index.json"
      with patch.object(sys, "platform", platform), patch.object(
        fcntl, "F_FULLFSYNC", 51, create=True
      ), patch.object(fcntl, "fcntl", fcntl_call), patch("os.fsync", fsync), patch(
        "os.replace", replace
      ):
        jsonio.write_bytes(target, CONTENT)
      self.assertEqual(target.read_bytes(), CONTENT)
    return events

  def test_WriteBytes_FullFsyncsFileBeforeReplaceOnDarwin(self) -> None:
    if os.name == "nt":
      self.skipTest("fcntl is POSIX only")
    events = self.write_recording_fullfsync("darwin")
    self.assertIn(("fullfsync", "file"), events)
    self.assertLess(events.index(("fullfsync", "file")), events.index(("replace", "index.json")))

  def test_WriteBytes_SkipsFullFsyncOffDarwin(self) -> None:
    if os.name == "nt":
      self.skipTest("fcntl is POSIX only")
    events = self.write_recording_fullfsync("linux")
    self.assertEqual(events, [("fsync", "file"), ("replace", "index.json"), ("fsync", "dir")])

  def test_WriteBytes_IgnoresUnsupportedDirectoryFsync(self) -> None:
    if os.name == "nt":  # pragma: no cover - POSIX only: a directory cannot be opened for fsync on Windows
      self.skipTest("a directory cannot be opened for fsync on Windows")
    for code in (errno.EINVAL, errno.ENOTSUP):
      with self.subTest(errno=errno.errorcode[code]), tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "index.json"
        dir_fsyncs: list[int] = []
        with patch("os.fsync", _dir_fsync_failing_with(code, dir_fsyncs)):
          jsonio.write_bytes(target, CONTENT)
        self.assertEqual(len(dir_fsyncs), 1)
        self.assertEqual(target.read_bytes(), CONTENT)

  def test_WriteBytes_RaisesOtherDirectoryFsyncError(self) -> None:
    if os.name == "nt":  # pragma: no cover - POSIX only: a directory cannot be opened for fsync on Windows
      self.skipTest("a directory cannot be opened for fsync on Windows")
    with tempfile.TemporaryDirectory() as tmp:
      target = Path(tmp) / "index.json"
      with patch("os.fsync", _dir_fsync_failing_with(errno.EIO, [])):
        with self.assertRaises(OSError) as raised:
          jsonio.write_bytes(target, CONTENT)
    self.assertEqual(raised.exception.errno, errno.EIO)


if __name__ == "__main__":
  unittest.main()
