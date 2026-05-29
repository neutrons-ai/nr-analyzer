"""
Configuration utilities for analyzer tools.

``.env`` cascade
----------------
When the first :func:`get_config` call is made (from any CLI entry point),
``.env`` files are loaded in **decreasing priority** (earlier loads win
because ``override=False`` is used for every call):

1. **Process environment** — real shell ``export`` values always win.
2. **Explicit path** — passed as ``get_config(dotenv_path=...)`` or via
   the ``ANALYZER_ENV_FILE`` environment variable.
3. **Project ``.env``** — the nearest ``.env`` found by walking upward
   from the current working directory (``dotenv.find_dotenv``). The
   walk-up means a single ``.env`` placed at a *repo root* applies to
   every sample sub-folder underneath it.
4. **User-global ``.env``** — ``$ANALYZER_CONFIG_DIR/.env`` if set,
   else ``$XDG_CONFIG_HOME/analyzer/.env`` if set,
   else ``~/.config/analyzer/.env``.

Path resolution
---------------
The pipeline needs five role-based directories: combined data, partial
data, models, results, and reports. Each role X is resolved as:

1. If absolute ``ANALYZER_X_DIR`` is set, use it verbatim.
2. Else use ``<project_dir>/<subdir>``, where ``<subdir>`` is
   ``ANALYZER_X_SUBDIR`` if set, else the lowercase default
   (``rawdata`` / ``models`` / ``results`` / ``reports``).
3. ``<project_dir>`` is ``$ANALYZER_PROJECT_DIR`` if set, else
   ``Path.cwd()``. The project root is **never** auto-derived from the
   loaded ``.env`` location, so a repo-level ``.env`` can define
   sub-folder *names* without forcing the repo to be the project root.

Special cases
-------------
- ``get_partial_data_dir()`` falls back to ``get_combined_data_dir()``
  when neither ``ANALYZER_PARTIAL_DATA_DIR`` nor
  ``ANALYZER_PARTIAL_SUBDIR`` is set. Most reduction setups put both
  kinds of files in one folder.

Variable names
--------------
Project anchor:
    ANALYZER_PROJECT_DIR        Absolute project root (default: cwd)

Sub-directory names (used relative to PROJECT_DIR):
    ANALYZER_DATA_SUBDIR        Combined data sub-folder    (default: rawdata)
    ANALYZER_PARTIAL_SUBDIR     Partial data sub-folder     (default: → DATA_SUBDIR)
    ANALYZER_MODELS_SUBDIR      Generated scripts           (default: models)
    ANALYZER_RESULTS_SUBDIR     Fit output dirs             (default: results)
    ANALYZER_REPORTS_SUBDIR     Generated reports           (default: reports)

Absolute overrides (legacy; win over the SUBDIR form):
    ANALYZER_RESULTS_DIR
    ANALYZER_COMBINED_DATA_DIR
    ANALYZER_PARTIAL_DATA_DIR
    ANALYZER_REPORTS_DIR
    ANALYZER_MODELS_DIR

Other:
    ANALYZER_COMBINED_DATA_TEMPLATE  File-name template (default: REFL_{set_id}_combined_data_auto.txt)
    ANALYZER_ENV_FILE                Extra ``.env`` loaded before project/global
    ANALYZER_CONFIG_DIR              Override directory for the user-global ``.env``
"""

import os
from pathlib import Path
from typing import List, Optional

try:
    from dotenv import load_dotenv as _load_dotenv  # type: ignore
    from dotenv import find_dotenv as _find_dotenv  # type: ignore
    _DOTENV_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DOTENV_AVAILABLE = False


# Lowercase, simple defaults. Override at the repo level by setting
# ANALYZER_*_SUBDIR in a .env that lives above the project folders.
_SUBDIR_DEFAULTS: dict[str, str] = {
    "ANALYZER_DATA_SUBDIR":     "rawdata",
    "ANALYZER_MODELS_SUBDIR":   "models",
    "ANALYZER_RESULTS_SUBDIR":  "results",
    "ANALYZER_REPORTS_SUBDIR":  "reports",
    # ANALYZER_PARTIAL_SUBDIR has no default — it falls back to DATA.
}

# Legacy absolute-path keys + non-path scalar (template).
_DEFAULTS: dict[str, str] = {
    "ANALYZER_COMBINED_DATA_TEMPLATE":  "REFL_{set_id}_combined_data_auto.txt",
}


def _user_global_env_path() -> Path:
    """Return the path to the user-global ``.env``.

    Order: ``$ANALYZER_CONFIG_DIR`` → ``$XDG_CONFIG_HOME/analyzer`` →
    ``~/.config/analyzer``.
    """
    explicit = os.environ.get("ANALYZER_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser() / ".env"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "analyzer" / ".env"


def _candidate_env_paths(dotenv_path: Optional[str]) -> List[Path]:
    """Return `.env` paths in the order they should be loaded.

    Earlier entries have higher priority because ``override=False``.
    The process environment is not returned here — it is already in
    ``os.environ`` and wins automatically.
    """
    paths: List[Path] = []

    # 1. Explicit caller-supplied path (or $ANALYZER_ENV_FILE).
    explicit = dotenv_path or os.environ.get("ANALYZER_ENV_FILE")
    if explicit:
        paths.append(Path(explicit).expanduser())

    # 2. Project .env — walk upward from CWD.
    if _DOTENV_AVAILABLE:
        found = _find_dotenv(usecwd=True)
        if found:
            paths.append(Path(found))

    # 3. User-global .env.
    paths.append(_user_global_env_path())

    # De-duplicate while preserving order; drop non-existent files.
    seen: set[str] = set()
    unique: List[Path] = []
    for p in paths:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            unique.append(p)
    return unique


def _load_env(dotenv_path: Optional[str] = None) -> List[Path]:
    """Load analyzer ``.env`` cascade. Returns the list of files loaded.

    All layers are loaded with ``override=False`` so the process
    environment and earlier (higher-priority) files win.
    """
    if not _DOTENV_AVAILABLE:
        return []
    loaded: List[Path] = []
    for path in _candidate_env_paths(dotenv_path):
        _load_dotenv(path, override=False)
        loaded.append(path)
    return loaded


class Config:
    """Centralized configuration manager backed by environment variables."""

    def __init__(self, dotenv_path: Optional[str] = None):
        """Load the ``.env`` cascade and record which files were used.

        Parameters
        ----------
        dotenv_path:
            Optional extra ``.env`` path inserted at the top of the cascade
            (after the process environment but before project and user-global
            files). Environment variables already set are **not** overridden
            (``override=False``).
        """
        self.loaded_env_files: List[Path] = _load_env(dotenv_path)

    # -- project root --------------------------------------------------------

    def get_project_dir(self) -> str:
        """Project root used to resolve sub-directory paths."""
        explicit = os.environ.get("ANALYZER_PROJECT_DIR")
        if explicit:
            return str(Path(explicit).expanduser())
        return str(Path.cwd())

    def _resolve(self, abs_key: str, subdir_key: str, default_subdir: str) -> str:
        """Return absolute override if set, else <project_dir>/<subdir>."""
        absolute = os.environ.get(abs_key)
        if absolute:
            return absolute
        subdir = os.environ.get(subdir_key, default_subdir)
        return str(Path(self.get_project_dir()) / subdir)

    # -- role-based path accessors ------------------------------------------

    def get_results_dir(self) -> str:
        return self._resolve(
            "ANALYZER_RESULTS_DIR",
            "ANALYZER_RESULTS_SUBDIR",
            _SUBDIR_DEFAULTS["ANALYZER_RESULTS_SUBDIR"],
        )

    def get_combined_data_dir(self) -> str:
        return self._resolve(
            "ANALYZER_COMBINED_DATA_DIR",
            "ANALYZER_DATA_SUBDIR",
            _SUBDIR_DEFAULTS["ANALYZER_DATA_SUBDIR"],
        )

    def get_partial_data_dir(self) -> str:
        # Absolute legacy override wins.
        absolute = os.environ.get("ANALYZER_PARTIAL_DATA_DIR")
        if absolute:
            return absolute
        # Explicit subdir wins next.
        subdir = os.environ.get("ANALYZER_PARTIAL_SUBDIR")
        if subdir:
            return str(Path(self.get_project_dir()) / subdir)
        # Otherwise fall back to the combined-data directory.
        return self.get_combined_data_dir()

    def get_reports_dir(self) -> str:
        return self._resolve(
            "ANALYZER_REPORTS_DIR",
            "ANALYZER_REPORTS_SUBDIR",
            _SUBDIR_DEFAULTS["ANALYZER_REPORTS_SUBDIR"],
        )

    def get_models_dir(self) -> str:
        return self._resolve(
            "ANALYZER_MODELS_DIR",
            "ANALYZER_MODELS_SUBDIR",
            _SUBDIR_DEFAULTS["ANALYZER_MODELS_SUBDIR"],
        )

    def get_combined_data_template(self) -> str:
        return os.environ.get(
            "ANALYZER_COMBINED_DATA_TEMPLATE",
            _DEFAULTS["ANALYZER_COMBINED_DATA_TEMPLATE"],
        )

    # Keep a generic accessor for forward compatibility.
    def get_path(self, key: str) -> str:
        """Return the value of an arbitrary ANALYZER_* config key.

        Recognizes the role-based getters by short name (``results_dir``,
        ``combined_data_dir``, ``partial_data_dir``, ``reports_dir``,
        ``models_dir``, ``combined_data_template``) so callers don't have to
        know whether the value is computed or a raw env var.
        """
        env_key = key if key.startswith("ANALYZER_") else f"ANALYZER_{key.upper()}"
        # Map the legacy absolute-path keys to the corresponding accessor so
        # SUBDIR overrides are honored.
        accessors = {
            "ANALYZER_RESULTS_DIR":            self.get_results_dir,
            "ANALYZER_COMBINED_DATA_DIR":      self.get_combined_data_dir,
            "ANALYZER_PARTIAL_DATA_DIR":       self.get_partial_data_dir,
            "ANALYZER_REPORTS_DIR":            self.get_reports_dir,
            "ANALYZER_MODELS_DIR":             self.get_models_dir,
            "ANALYZER_COMBINED_DATA_TEMPLATE": self.get_combined_data_template,
        }
        if env_key in accessors:
            return accessors[env_key]()
        return os.environ[env_key]


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_config_instance: Optional[Config] = None


def get_config(dotenv_path: Optional[str] = None) -> Config:
    """Return the global :class:`Config` instance (created on first call)."""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config(dotenv_path)
    return _config_instance


def get_data_organization_info() -> dict:
    """Return current data-directory layout as a plain dict."""
    config = get_config()
    return {
        "combined_data_dir":        config.get_combined_data_dir(),
        "partial_data_dir":         config.get_partial_data_dir(),
        "reports_dir":              config.get_reports_dir(),
        "results_dir":              config.get_results_dir(),
        "combined_data_template":   config.get_combined_data_template(),
        "models_dir":               config.get_models_dir(),
    }
