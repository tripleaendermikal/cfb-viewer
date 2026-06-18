"""Shared paths for cfb-viewer pipeline scripts."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_DIR = Path(__file__).resolve().parent
DATA_ROOT = REPO_ROOT.parent
