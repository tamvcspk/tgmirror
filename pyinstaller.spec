# Phase 14 (docs/06-lo-trinh.md, "Phase 14"): builds the `tgmirror` onedir binary attached to a
# GitHub Release. Run from the repo root after `uv sync --group pyinstaller`:
#
#   uv run pyinstaller pyinstaller.spec
#
# Output lands in dist/tgmirror/ (an onedir build, not `onefile` — see the roadmap note on why).
#
# The hidden imports/datas below are for packages this project loads dynamically rather than by a
# plain `import` PyInstaller's static analysis can follow (roadmap, "Chỗ PyInstaller hay gãy"):
# keyring finds its backends through `importlib.metadata` entry points (needs both the backend
# modules themselves and the package's own dist-info metadata copied in), hachoir's parser
# registry walks its own subpackages at runtime, and prompt_toolkit (via questionary) picks its
# input/output backend by platform at import time. `schema.sql` is read via
# `importlib.resources` (store/db.py::_sql), which PyInstaller's import scan cannot see either.
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

hiddenimports = (
    collect_submodules("keyring.backends")
    + collect_submodules("hachoir.parser")
    + collect_submodules("prompt_toolkit")
)
datas = copy_metadata("keyring") + [("src/tgmirror/store/schema.sql", "tgmirror/store")]

a = Analysis(
    ["scripts/pyinstaller_entry.py"],
    pathex=["src"],
    hiddenimports=hiddenimports,
    datas=datas,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tgmirror",
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="tgmirror",
)
