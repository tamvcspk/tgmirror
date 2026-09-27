"""Entry point for the PyInstaller build (Phase 14, docs/06-lo-trinh.md).

`pyproject.toml`'s `[project.scripts]` entry point works for pip/uv installs but PyInstaller
needs an actual script file to analyze, so this just calls the same Typer `app`.
"""

from tgmirror.cli.app import app

if __name__ == "__main__":
    app()
