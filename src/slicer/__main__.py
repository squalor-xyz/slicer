#!/usr/bin/env python3
"""Allow `python3 -m slicer` with nothing installed."""

from __future__ import annotations

from slicer.cli import main

raise SystemExit(main())
