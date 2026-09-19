"""tgmirror — clone a Telegram channel you joined into another channel."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tgmirror")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0+unknown"
