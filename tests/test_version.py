"""Installed package metadata is generated from pyproject.toml in CI."""

from importlib.metadata import version

import stompy_ticketing


def test_exported_version_matches_build_metadata():
    assert stompy_ticketing.__version__ == version("stompy-ticketing")
