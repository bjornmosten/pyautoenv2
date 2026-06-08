# pyautoenv2 Automatically activate and deactivate Python environments.
# Copyright (C) 2023  Harry Saunders.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Tests for the per-directory '.envrc' feature."""

import os
from io import StringIO

import pytest

import pyautoenv2
from tests.tools import clear_lru_caches, root_dir


@pytest.fixture(autouse=True)
def _clean_env():
    """Run each test with an empty, isolated environment."""
    clear_lru_caches(pyautoenv2)
    saved = os.environ
    os.environ = {}  # noqa: B003
    try:
        yield
    finally:
        os.environ = saved  # noqa: B003


def make_envrc(fs, directory, contents):
    """Create a '.envrc' with the given contents under ``directory``."""
    fs.create_file(os.path.join(str(directory), ".envrc"), contents=contents)


class TestParseEnvrc:
    def test_parses_plain_and_exported_assignments(self, fs):
        path = str(root_dir() / ".envrc")
        fs.create_file(path, contents="FOO=bar\nexport BAZ=qux\n")

        assert pyautoenv2.parse_envrc(path) == [("FOO", "bar"), ("BAZ", "qux")]

    def test_ignores_comments_and_blank_lines(self, fs):
        path = str(root_dir() / ".envrc")
        fs.create_file(path, contents="# a comment\n\n  \nFOO=bar\n")

        assert pyautoenv2.parse_envrc(path) == [("FOO", "bar")]

    def test_strips_surrounding_quotes(self, fs):
        path = str(root_dir() / ".envrc")
        fs.create_file(
            path,
            contents="FOO=\"a b\"\nBAR='c d'\n",
        )

        assert pyautoenv2.parse_envrc(path) == [("FOO", "a b"), ("BAR", "c d")]

    def test_skips_command_substitution(self, fs):
        path = str(root_dir() / ".envrc")
        fs.create_file(
            path,
            contents="FOO=$(whoami)\nBAR=`id`\nBAZ=ok\n",
        )

        assert pyautoenv2.parse_envrc(path) == [("BAZ", "ok")]

    def test_skips_non_assignment_lines(self, fs):
        path = str(root_dir() / ".envrc")
        fs.create_file(
            path,
            contents="PATH_add ./bin\nsource other\nFOO=ok\n",
        )

        assert pyautoenv2.parse_envrc(path) == [("FOO", "ok")]

    def test_last_assignment_wins(self, fs):
        path = str(root_dir() / ".envrc")
        fs.create_file(path, contents="FOO=one\nFOO=two\n")

        assert pyautoenv2.parse_envrc(path) == [("FOO", "two")]

    def test_missing_file_returns_empty(self):
        missing = str(root_dir() / "nope" / ".envrc")
        assert pyautoenv2.parse_envrc(missing) == []


class TestDiscoverEnvrc:
    def test_finds_envrc_in_directory(self, fs):
        proj = root_dir() / "proj"
        make_envrc(fs, proj, "FOO=bar\n")

        assert pyautoenv2.discover_envrc(str(proj)) == str(proj)

    def test_finds_envrc_in_parent(self, fs):
        proj = root_dir() / "proj"
        make_envrc(fs, proj, "FOO=bar\n")
        child = proj / "src" / "pkg"
        fs.create_dir(str(child))

        assert pyautoenv2.discover_envrc(str(child)) == str(proj)

    def test_returns_none_when_absent(self, fs):
        proj = root_dir() / "proj"
        fs.create_dir(str(proj))

        assert pyautoenv2.discover_envrc(str(proj)) is None

    def test_respects_ignored_dirs(self, fs):
        proj = root_dir() / "proj"
        make_envrc(fs, proj, "FOO=bar\n")
        os.environ[pyautoenv2.IGNORE_DIRS] = str(proj)

        assert pyautoenv2.discover_envrc(str(proj)) is None


class TestEnvrcCommand:
    def test_load_exports_vars_and_tracking_state(self, fs):
        proj = root_dir() / "proj"
        make_envrc(fs, proj, "FOO=bar\nBAZ=qux\n")

        cmd = pyautoenv2.envrc_command(str(proj))

        assert "export FOO='bar'" in cmd
        assert "export BAZ='qux'" in cmd
        assert f"export {pyautoenv2.ENVRC_DIR}='{proj}'" in cmd
        assert f"export {pyautoenv2.ENVRC_VARS}='FOO;BAZ'" in cmd

    def test_no_command_when_no_envrc(self, fs):
        proj = root_dir() / "proj"
        fs.create_dir(str(proj))

        assert pyautoenv2.envrc_command(str(proj)) == ""

    def test_no_command_when_staying_in_same_envrc(self, fs):
        proj = root_dir() / "proj"
        make_envrc(fs, proj, "FOO=bar\n")
        os.environ[pyautoenv2.ENVRC_DIR] = str(proj)
        os.environ[pyautoenv2.ENVRC_VARS] = "FOO"

        assert pyautoenv2.envrc_command(str(proj)) == ""

    def test_unload_unsets_vars_on_leave(self, fs):
        proj = root_dir() / "proj"
        outside = root_dir() / "elsewhere"
        fs.create_dir(str(outside))
        os.environ[pyautoenv2.ENVRC_DIR] = str(proj)
        os.environ[pyautoenv2.ENVRC_VARS] = "FOO;BAZ"
        os.environ[pyautoenv2.ENVRC_BACKUP] = ""

        cmd = pyautoenv2.envrc_command(str(outside))

        assert "unset FOO" in cmd
        assert "unset BAZ" in cmd
        assert pyautoenv2.ENVRC_DIR in cmd

    def test_unload_restores_previous_values(self, fs):
        proj = root_dir() / "proj"
        outside = root_dir() / "elsewhere"
        fs.create_dir(str(outside))
        os.environ[pyautoenv2.ENVRC_DIR] = str(proj)
        os.environ[pyautoenv2.ENVRC_VARS] = "FOO"
        os.environ[pyautoenv2.ENVRC_BACKUP] = pyautoenv2._encode_envrc_backup(
            [("FOO", "original")],
        )

        cmd = pyautoenv2.envrc_command(str(outside))

        assert "export FOO='original'" in cmd
        assert "unset FOO" not in cmd

    def test_load_backs_up_preexisting_value(self, fs):
        proj = root_dir() / "proj"
        make_envrc(fs, proj, "FOO=new\n")
        os.environ["FOO"] = "preexisting"

        cmd = pyautoenv2.envrc_command(str(proj))
        backup_token = f"export {pyautoenv2.ENVRC_BACKUP}='"
        encoded = cmd.split(backup_token, 1)[1].split("'", 1)[0]
        decoded = pyautoenv2._decode_envrc_backup(encoded)

        assert decoded == {"FOO": "preexisting"}

    def test_switching_envrc_unloads_then_loads(self, fs):
        proj_a = root_dir() / "a"
        proj_b = root_dir() / "b"
        make_envrc(fs, proj_a, "AAA=1\n")
        make_envrc(fs, proj_b, "BBB=2\n")
        os.environ[pyautoenv2.ENVRC_DIR] = str(proj_a)
        os.environ[pyautoenv2.ENVRC_VARS] = "AAA"
        os.environ[pyautoenv2.ENVRC_BACKUP] = ""
        os.environ["AAA"] = "1"

        cmd = pyautoenv2.envrc_command(str(proj_b))

        assert "unset AAA" in cmd
        assert "export BBB='2'" in cmd
        assert cmd.index("unset AAA") < cmd.index("export BBB='2'")

    def test_backup_roundtrip(self):
        pairs = [("A", "1"), ("B", "value with spaces"), ("C", "x=y")]
        encoded = pyautoenv2._encode_envrc_backup(pairs)

        assert pyautoenv2._decode_envrc_backup(encoded) == dict(pairs)


class TestMainIntegration:
    def test_main_emits_envrc_with_no_venv(self, fs):
        proj = root_dir() / "proj"
        make_envrc(fs, proj, "FOO=bar\n")
        stdout = StringIO()

        assert pyautoenv2.main([str(proj)], stdout) == 0
        assert "export FOO='bar'" in stdout.getvalue()

    def test_main_combines_venv_and_envrc(self, fs):
        proj = root_dir() / "proj"
        activator = proj / ".venv" / "bin" / "activate"
        fs.create_file(str(activator))
        make_envrc(fs, proj, "FOO=bar\n")
        stdout = StringIO()

        assert pyautoenv2.main([str(proj)], stdout) == 0
        out = stdout.getvalue()
        assert f". '{activator}'" in out
        assert "export FOO='bar'" in out
        assert out.index("activate") < out.index("export FOO")
