"""Every module must import cleanly.

This exists because the bot once shipped a NameError in a type annotation: the
import was removed, the annotation was not, and nothing imported that module in
the tests. It crash-looped on the Pi instead of failing here.
"""

import importlib
import pkgutil

import pytest

import src

MODULES = sorted(module.name for module in pkgutil.walk_packages(src.__path__, "src."))


def test_every_module_is_discovered():
    assert len(MODULES) > 15, "walk_packages found suspiciously little"


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name):
    importlib.import_module(module_name)


def test_the_entry_point_imports():
    """bot.py is what the Pi actually runs."""
    importlib.import_module("src.app")
