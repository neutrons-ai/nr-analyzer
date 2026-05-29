"""
Tests for analyzer_tools.config_utils module.

Configuration is read from environment variables (optionally loaded from a
.env file via python-dotenv). Tests use ``monkeypatch`` to set env vars and
reset the global singleton between tests.
"""

import os
from pathlib import Path

import pytest

import analyzer_tools.config_utils as config_mod
from analyzer_tools.config_utils import Config, get_config, get_data_organization_info

# Captured before any autouse fixture can stub it.
_real_load_env = config_mod._load_env

_ALL_KEYS = (
    list(config_mod._DEFAULTS)
    + list(config_mod._SUBDIR_DEFAULTS)
    + [
        "ANALYZER_PROJECT_DIR",
        "ANALYZER_PARTIAL_SUBDIR",
        "ANALYZER_RESULTS_DIR",
        "ANALYZER_COMBINED_DATA_DIR",
        "ANALYZER_PARTIAL_DATA_DIR",
        "ANALYZER_REPORTS_DIR",
        "ANALYZER_MODELS_DIR",
    ]
)


@pytest.fixture(autouse=True)
def reset_singleton(monkeypatch):
    """Reset the global _config_instance and disable the .env cascade."""
    monkeypatch.setattr(config_mod, "_config_instance", None)
    for key in _ALL_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config_mod, "_load_env", lambda dotenv_path=None: [])
    yield


class TestProjectDirAndDefaults:
    def test_project_dir_defaults_to_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert Config().get_project_dir() == str(tmp_path)

    def test_project_dir_from_env(self, monkeypatch):
        monkeypatch.setenv("ANALYZER_PROJECT_DIR", "/custom/proj")
        assert Config().get_project_dir() == "/custom/proj"

    def test_default_subdirs_under_project(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANALYZER_PROJECT_DIR", str(tmp_path))
        c = Config()
        assert c.get_combined_data_dir() == str(tmp_path / "rawdata")
        assert c.get_models_dir() == str(tmp_path / "models")
        assert c.get_results_dir() == str(tmp_path / "results")
        assert c.get_reports_dir() == str(tmp_path / "reports")
        # Partial falls back to combined when no override is set.
        assert c.get_partial_data_dir() == c.get_combined_data_dir()

    def test_template_default(self):
        assert (
            Config().get_combined_data_template()
            == "REFL_{set_id}_combined_data_auto.txt"
        )


class TestSubdirOverrides:
    def test_data_subdir_capitalized(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANALYZER_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("ANALYZER_DATA_SUBDIR", "Rawdata")
        c = Config()
        assert c.get_combined_data_dir() == str(tmp_path / "Rawdata")
        # Partial inherits the combined-data override.
        assert c.get_partial_data_dir() == str(tmp_path / "Rawdata")

    def test_models_results_reports_subdirs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANALYZER_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("ANALYZER_MODELS_SUBDIR", "Models")
        monkeypatch.setenv("ANALYZER_RESULTS_SUBDIR", "analyzer_results")
        monkeypatch.setenv("ANALYZER_REPORTS_SUBDIR", "Reports")
        c = Config()
        assert c.get_models_dir() == str(tmp_path / "Models")
        assert c.get_results_dir() == str(tmp_path / "analyzer_results")
        assert c.get_reports_dir() == str(tmp_path / "Reports")

    def test_partial_subdir_separate_from_data(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANALYZER_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("ANALYZER_DATA_SUBDIR", "combined")
        monkeypatch.setenv("ANALYZER_PARTIAL_SUBDIR", "partials")
        c = Config()
        assert c.get_combined_data_dir() == str(tmp_path / "combined")
        assert c.get_partial_data_dir() == str(tmp_path / "partials")


class TestAbsoluteOverrides:
    def test_absolute_results_dir_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANALYZER_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("ANALYZER_RESULTS_SUBDIR", "subdir_should_be_ignored")
        monkeypatch.setenv("ANALYZER_RESULTS_DIR", "/abs/results")
        assert Config().get_results_dir() == "/abs/results"

    def test_absolute_partial_dir_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANALYZER_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("ANALYZER_PARTIAL_DATA_DIR", "/abs/partial")
        c = Config()
        assert c.get_partial_data_dir() == "/abs/partial"
        # Combined still resolves under the project.
        assert c.get_combined_data_dir() == str(tmp_path / "rawdata")


class TestRepoLevelDotenv:
    def test_repo_level_dotenv_supplies_subdirs(self, tmp_path, monkeypatch):
        """A .env at a repo root above the sample dir defines SUBDIR names;
        cwd remains the project root."""
        repo = tmp_path / "repo"
        sample = repo / "Sample7"
        sample.mkdir(parents=True)
        (repo / ".env").write_text(
            "ANALYZER_DATA_SUBDIR=Rawdata\n"
            "ANALYZER_MODELS_SUBDIR=Models\n"
            "ANALYZER_RESULTS_SUBDIR=analyzer_results\n"
        )
        monkeypatch.setattr(config_mod, "_load_env", _real_load_env)
        monkeypatch.chdir(sample)
        # Disable user-global so the test isn't contaminated.
        monkeypatch.setenv("ANALYZER_CONFIG_DIR", str(tmp_path / "no_such"))

        c = Config()
        assert c.get_project_dir() == str(sample)
        assert c.get_combined_data_dir() == str(sample / "Rawdata")
        assert c.get_models_dir() == str(sample / "Models")
        assert c.get_results_dir() == str(sample / "analyzer_results")
        # Reports stays the lowercase default.
        assert c.get_reports_dir() == str(sample / "reports")
        # Partial inherits combined.
        assert c.get_partial_data_dir() == str(sample / "Rawdata")


class TestDotenvCascade:
    def test_dotenv_file_is_loaded(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_mod, "_load_env", _real_load_env)
        env_file = tmp_path / ".env"
        env_file.write_text("ANALYZER_RESULTS_DIR=/from/dotenv\n")
        monkeypatch.delenv("ANALYZER_RESULTS_DIR", raising=False)
        config = Config(dotenv_path=str(env_file))
        assert config.get_results_dir() == "/from/dotenv"

    def test_env_var_wins_over_dotenv(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_mod, "_load_env", _real_load_env)
        env_file = tmp_path / ".env"
        env_file.write_text("ANALYZER_RESULTS_DIR=/from/dotenv\n")
        monkeypatch.setenv("ANALYZER_RESULTS_DIR", "/from/shell")
        config = Config(dotenv_path=str(env_file))
        assert config.get_results_dir() == "/from/shell"


class TestGenericAccessor:
    def test_get_path_resolves_role_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANALYZER_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("ANALYZER_REPORTS_SUBDIR", "Reports")
        c = Config()
        # Both ANALYZER_-prefixed and short-name forms route through the
        # accessor so SUBDIR overrides apply.
        assert c.get_path("ANALYZER_REPORTS_DIR") == str(tmp_path / "Reports")
        assert c.get_path("reports_dir") == str(tmp_path / "Reports")


class TestGlobalConfig:
    def test_get_config_returns_singleton(self):
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_get_data_organization_info_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANALYZER_PROJECT_DIR", str(tmp_path))
        info = get_data_organization_info()
        expected = {
            "combined_data_dir", "partial_data_dir", "reports_dir",
            "results_dir", "combined_data_template", "models_dir",
        }
        assert set(info.keys()) == expected

    def test_get_data_organization_info_values_are_strings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANALYZER_PROJECT_DIR", str(tmp_path))
        info = get_data_organization_info()
        for k, v in info.items():
            assert isinstance(v, str), f"Expected str for {k}, got {type(v)}"
