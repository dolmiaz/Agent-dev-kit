#!/usr/bin/env python3
"""skillsync の入口(本体は kit/.agent-kit/bin/skillsync。導入先では .agent-kit/bin/skillsync として使える)"""
import runpy
import sys
from pathlib import Path

sys.argv[0] = str(Path(__file__).resolve().parents[1] / "kit" / ".agent-kit" / "bin" / "skillsync")
runpy.run_path(sys.argv[0], run_name="__main__")
