from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_DIRS = [
    "cloud/common/schemas",
    "cloud/common/db",
    "cloud/ingest_worker",
    "cloud/api",
    "cloud/migrations",
    "cloud/tests",
    "edge",
    "frontend",
    "shared/contracts",
    "footage",
]

REQUIRED_FILES = [
    "pyproject.toml",
    ".gitignore",
    "Makefile",
    "README.md",
    "cloud/common/__init__.py",
    "cloud/common/schemas/__init__.py",
]


def test_required_dirs_exist() -> None:
    missing = [d for d in REQUIRED_DIRS if not (REPO_ROOT / d).is_dir()]
    assert not missing, f"missing directories: {missing}"


def test_required_files_exist() -> None:
    missing = [f for f in REQUIRED_FILES if not (REPO_ROOT / f).is_file()]
    assert not missing, f"missing files: {missing}"


def test_makefile_has_core_targets() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("up:", "down:", "logs:", "test:", "migrate:", "topics:"):
        assert target in makefile, f"Makefile missing target {target!r}"
