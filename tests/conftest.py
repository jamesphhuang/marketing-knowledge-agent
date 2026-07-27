from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def production_search_alias_pre_activation_repo(tmp_path_factory):
    formal_root = Path(__file__).resolve().parents[1]
    root = tmp_path_factory.mktemp("production-search-alias-pre-activation")

    for name in (".git", "data", "obsidian_vault", "reports"):
        (root / name).symlink_to(formal_root / name, target_is_directory=True)

    mka = root / ".mka"
    mka.mkdir()
    (mka / "content_index.sqlite").symlink_to(
        formal_root / ".mka/content_index.sqlite"
    )

    source = formal_root / "src/marketing_knowledge_agent"
    destination = root / "src/marketing_knowledge_agent"
    destination.mkdir(parents=True)
    for path in source.iterdir():
        if path.name in {"__pycache__", "pipeline.py", "search_aliases.py"}:
            continue
        (destination / path.name).symlink_to(path, target_is_directory=path.is_dir())

    backup_pipeline = (
        formal_root
        / "data/governance/backups/production-search-alias-plan-v2-668c2856f39124ae/pipeline.py"
    )
    shutil.copy2(backup_pipeline, destination / "pipeline.py")
    (root / "tests").mkdir()
    return root
