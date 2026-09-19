"""Filesystem locations for config, state and sessions (docs/02-cli-ux.md, "Config")."""

import re
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path, user_data_path

APP_NAME = "tgmirror"
_SESSION_NAME = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class Paths:
    config_dir: Path
    data_dir: Path

    @classmethod
    def default(cls) -> "Paths":
        """OS-specific locations via platformdirs."""
        return cls(
            config_dir=user_config_path(APP_NAME, appauthor=False),
            data_dir=user_data_path(APP_NAME, appauthor=False),
        )

    @classmethod
    def under(cls, root: Path) -> "Paths":
        """Everything below ``root`` (tests, portable installs)."""
        return cls(config_dir=root / "config", data_dir=root / "data")

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tgmirror.db"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def tmp_dir(self) -> Path:
        """Scratch space for strategy B downloads (``tmp/<job>/<msg_id>``)."""
        return self.data_dir / "tmp"

    def session_path(self, name: str = "default") -> Path:
        """Path of the Telethon session file. It is a secret: never log or commit it."""
        if not _SESSION_NAME.fullmatch(name):
            raise ValueError(f"invalid session name {name!r}: use letters, digits, '_' or '-'")
        return self.sessions_dir / f"{name}.session"

    def ensure(self) -> None:
        """Create the directories. Sessions dir is owner-only where the OS supports it."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(mode=0o700, exist_ok=True)
        self.tmp_dir.mkdir(exist_ok=True)
