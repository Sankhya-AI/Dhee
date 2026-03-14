"""Skill discovery — filesystem scanning for SKILL.md files.

Discovers skill directories in standard locations and scans them
for skill files.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from engram.skills.schema import Skill


def discover_skill_dirs(repo_path: Optional[str] = None) -> List[str]:
    """Discover standard skill directories.

    Returns list of directories that may contain SKILL.md files:
    1. {repo}/.engram/skills/  (project-local skills)
    2. ~/.engram/skills/       (global user skills)
    """
    dirs = []

    # Project-local skills
    if repo_path:
        local_dir = os.path.join(repo_path, ".engram", "skills")
        dirs.append(local_dir)

    # Global user skills
    global_dir = os.path.join(os.path.expanduser("~"), ".engram", "skills")
    dirs.append(global_dir)

    return dirs


def scan_skill_files(dirs: List[str]) -> List[Tuple[str, str]]:
    """Scan directories for SKILL.md files.

    Returns list of (file_path, skill_id) tuples.
    """
    results = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for filename in os.listdir(d):
            if not filename.endswith(".skill.md"):
                continue
            skill_id = filename.replace(".skill.md", "")
            filepath = os.path.join(d, filename)
            results.append((filepath, skill_id))
    return results


def load_skill_file(path: str) -> Skill:
    """Load a single SKILL.md file into a Skill object."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return Skill.from_skill_md(content)
