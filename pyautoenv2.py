#!/usr/bin/env python3
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
"""
Print a command to activate or deactivate a Python venv based on a directory.

Supports environments managed by venv or poetry. A poetry project
directory must contain a 'poetry.lock' file. A venv project must contain
a directory called '.venv' or one of the names in the
'PYAUTOENV_VENV_NAME' environment variable (names separated by a ';').

To specify specific directories where pyautoenv2 should not activate
environments, add the directory's path to the 'PYAUTOENV_IGNORE_DIR'
environment variable. Paths should be separated using a ';'.

In addition to Python environments, pyautoenv2 can load per-directory
environment variables from a '.envrc' file. When you enter a directory
(or any of its children) that contains a '.envrc' file, the simple
'KEY=value' assignments within it are exported, and they are unset (or
restored to their previous values) again when you leave. Only literal
assignments are parsed; lines using command substitution or other shell
logic are ignored, so no arbitrary code from the file is ever executed.

When running the script with __debug__, the logging level can be set
using the 'PYAUTOENV_LOG_LEVEL' environment variable. The level can be
set to any supported by Python's 'logging' module.
"""

import os
import re
import sys
from functools import lru_cache
from io import StringIO
from typing import Dict, Iterator, List, TextIO, Tuple, Union

__version__ = "0.7.1"

CLI_HELP = f"""usage: pyautoenv2 [-h] [-V] [--fish | --pwsh] [directory]
{__doc__}
positional arguments:
  directory      the path to look in for a python environment (default: '.')

options:
  --fish         use fish activation script
  --pwsh         use powershell activation script
  -h, --help     show this help message and exit
  -V, --version  show program's version number and exit
"""
IGNORE_DIRS = "PYAUTOENV_IGNORE_DIR"
"""Directories to ignore and not activate environments within."""
VENV_NAMES = "PYAUTOENV_VENV_NAME"
"""Directory names to search in for venv virtual environments."""
DISMISSED_RELOCATIONS = "PYAUTOENV_DISMISSED_RELOCATIONS"
"""Venvs whose relocation prompt the user dismissed in this session."""
ENVRC_NAME = ".envrc"
"""Name of the file holding per-directory environment variables."""
ENVRC_DIR = "PYAUTOENV_ENVRC_DIR"
"""Directory whose '.envrc' is currently loaded."""
ENVRC_VARS = "PYAUTOENV_ENVRC_VARS"
"""';'-separated names of the variables set by the loaded '.envrc'."""
ENVRC_BACKUP = "PYAUTOENV_ENVRC_BACKUP"
"""Base64-encoded previous values of variables the '.envrc' overrode."""

_ENVRC_ASSIGNMENT = re.compile(
    r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$",
)
"""Matches a simple 'KEY=value' or 'export KEY=value' assignment."""

OS_LINUX = 0
OS_MACOS = 1
OS_WINDOWS = 2


if __debug__:
    import logging

    LOG_LEVEL = "PYAUTOENV_LOG_LEVEL"
    """The level to set the logger at."""

    logging.basicConfig(
        level=getattr(
            logging,
            os.environ.get(LOG_LEVEL, "DEBUG").upper(),
            logging.DEBUG,
        ),
        stream=sys.stderr,
        format="%(name)s: %(levelname)s: [%(asctime)s]: %(message)s",
    )
    logger = logging.getLogger("pyautoenv2")


class Args:
    """Container for command line arguments."""

    def __init__(
        self,
        directory: str,
        *,
        fish: bool = False,
        pwsh: bool = False,
    ) -> None:
        self.directory = directory
        self.fish = fish
        self.pwsh = pwsh


def main(sys_args: List[str], stdout: TextIO) -> int:  # noqa: C901
    """Write commands to activate/deactivate environments."""
    if __debug__:
        logger.debug("main(%s)", sys_args)
    if sys_args[:1] == ["--repair"]:
        return _run_repair(sys_args[1:])
    args = parse_args(sys_args, stdout)
    if not os.path.isdir(args.directory):
        if __debug__:
            logger.warning("path '%s' is not a directory", args.directory)
        return 1
    # 'discover_env' walks 'args.directory' up to the root, mutating it,
    # so capture the target directory now for the '.envrc' search below.
    target_directory = args.directory
    venv_buf = StringIO()
    new_activator = discover_env(args)
    active_env_dir = active_environment()
    if new_activator and is_local_venv_activator(new_activator):
        venv_dir = activator_venv_dir(new_activator)
        if venv_is_relocated(venv_dir) and not is_relocation_dismissed(
            venv_dir,
        ):
            emit_relocation_prompt(stdout, venv_dir, new_activator, args)
            return 0
    if active_env_dir:
        if not new_activator:
            deactivate(venv_buf)
        elif not activator_in_venv(new_activator, active_env_dir):
            deactivate_and_activate(venv_buf, new_activator)
    elif new_activator:
        activate(venv_buf, new_activator)
    commands = [venv_buf.getvalue(), envrc_command(target_directory)]
    stdout.write("; ".join(c for c in commands if c))
    return 0


def activate(stream: TextIO, activator: str) -> None:
    """Write the command to execute the given venv activator."""
    command = f". '{activator}'"
    if __debug__:
        logger.debug("activate: '%s'", command)
    stream.write(command)


def deactivate(stream: TextIO) -> None:
    """Write the deactivation command to the given stream."""
    command = "deactivate"
    if __debug__:
        logger.debug("deactivate: '%s'", command)
    stream.write(command)


def deactivate_and_activate(stream: TextIO, new_activator: str) -> None:
    """Write command to deactivate the current env and activate another."""
    command = f"deactivate && . '{new_activator}'"
    if __debug__:
        logger.debug("deactivate_and_activate: '%s'", command)
    stream.write(command)


def activator_in_venv(activator_path: str, venv_dir: str) -> bool:
    """Return True if the given activator is in the given venv directory."""
    candidate = activator_venv_dir(activator_path)
    try:
        return os.path.samefile(candidate, venv_dir)
    except OSError:
        # The active venv path no longer exists on disk (e.g. the user
        # moved or deleted the directory containing it). Fall back to a
        # normalised path comparison so we can still emit a sane command.
        norm_a = os.path.normpath(os.path.abspath(candidate))
        norm_b = os.path.normpath(os.path.abspath(venv_dir))
        return norm_a == norm_b


def activator_venv_dir(activator_path: str) -> str:
    """Return the venv directory that contains the given activator."""
    return os.path.dirname(os.path.dirname(activator_path))


def active_environment() -> Union[str, None]:
    """Return the directory of the currently active environment."""
    active_env_dir = os.environ.get("VIRTUAL_ENV")
    if __debug__:
        logger.debug("active_environment: '%s'", active_env_dir)
    return active_env_dir


def parse_args(argv: List[str], stdout: TextIO) -> Args:
    """Parse the sequence of command line arguments."""
    # Avoiding argparse gives a good speed boost and the parsing logic
    # is not too complex. We won't get a full 'bells and whistles' CLI
    # experience, but that's fine for our use-case.
    if not argv:
        return Args(os.getcwd())

    def parse_flag(argv: List[str], flag: str) -> bool:
        try:
            del argv[argv.index(flag)]
        except ValueError:
            return False
        return True

    fish = parse_flag(argv, "--fish")
    pwsh = parse_flag(argv, "--pwsh")
    num_activators = sum([fish, pwsh])
    if num_activators > 1:
        raise ValueError(
            f"zero or one activator flag expected, found {num_activators}",
        )
    if not argv:
        return Args(os.getcwd(), fish=fish, pwsh=pwsh)

    def parse_exit_flag(argv: List[str], flags: List[str]) -> bool:
        return any(f in argv for f in flags)

    if parse_exit_flag(argv, ["-h", "--help"]):
        stdout.write(CLI_HELP)
        sys.exit(0)
    if parse_exit_flag(argv, ["-V", "--version"]):
        stdout.write(f"pyautoenv2 {__version__}\n")
        sys.exit(0)

    # Ignore empty arguments.
    argv = [a for a in argv if a.strip()]
    if len(argv) > 1:
        raise ValueError(
            f"exactly one positional argument expected, found {len(argv)}",
        )
    directory = os.path.abspath(argv[0]) if argv else os.getcwd()
    return Args(directory=directory, fish=fish, pwsh=pwsh)


def discover_env(args: Args) -> Union[str, None]:
    """Find an environment activator in or above the given directory."""
    while (not dir_is_ignored(args.directory)) and (
        args.directory != os.path.dirname(args.directory)
    ):
        env_activator = get_virtual_env(args)
        if env_activator:
            if __debug__:
                logger.debug("discover_env: '%s'", env_activator)
            return env_activator
        args.directory = os.path.dirname(args.directory)
    if __debug__:
        logger.debug("discover_env: 'None'")
    return None


def dir_is_ignored(directory: str) -> bool:
    """Return True if the given directory is marked to be ignored."""
    return directory in ignored_dirs()


@lru_cache(maxsize=1)
def ignored_dirs() -> List[str]:
    """Get the list of directories to not activate an environment within."""
    dirs = os.environ.get(IGNORE_DIRS)
    if dirs:
        return dirs.split(";")
    return []


def envrc_command(directory: str) -> str:
    """
    Return the command to sync '.envrc' variables for the given directory.

    Compares the '.envrc' that applies to ``directory`` against the one
    currently loaded (recorded in ``PYAUTOENV_ENVRC_DIR``) and returns a
    POSIX-shell command that unloads the old one and/or loads the new
    one. Returns an empty string when nothing needs to change.
    """
    new_dir = discover_envrc(directory)
    old_dir = os.environ.get(ENVRC_DIR) or None
    if _same_envrc_dir(old_dir, new_dir):
        return ""
    parts: List[str] = []
    if old_dir:
        parts.append(_envrc_unload_command())
    if new_dir:
        # When moving directly between two '.envrc' directories the old
        # one's variables are still present in this process's environment,
        # so back up against the environment as it will be *after* the
        # unload runs, not the current one.
        base_env = _env_after_unload() if old_dir else dict(os.environ)
        parts.append(_envrc_load_command(new_dir, base_env))
    command = "; ".join(p for p in parts if p)
    if __debug__:
        logger.debug("envrc_command: '%s'", command)
    return command


def discover_envrc(directory: str) -> Union[str, None]:
    """Return nearest directory at or above ``directory`` with a '.envrc'."""
    current = directory
    while (not dir_is_ignored(current)) and (
        current != os.path.dirname(current)
    ):
        if os.path.isfile(os.path.join(current, ENVRC_NAME)):
            return current
        current = os.path.dirname(current)
    return None


def parse_envrc(path: str) -> List[Tuple[str, str]]:
    """
    Parse the simple 'KEY=value' assignments from a '.envrc' file.

    Only literal assignments are returned. Comments, blank lines, and any
    line that isn't a plain assignment (e.g. one using command
    substitution) are ignored so that no part of the file is ever
    executed. Later assignments to the same key win.
    """
    pairs: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as envrc_file:
            for raw_line in envrc_file:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                match = _ENVRC_ASSIGNMENT.match(line)
                if not match:
                    continue
                value = _envrc_value(match.group(2))
                if value is None:
                    continue
                pairs[match.group(1)] = value
    except OSError:
        return []
    return list(pairs.items())


def _envrc_value(raw: str) -> Union[str, None]:
    """
    Return the literal value of an assignment, or None to skip the line.

    Surrounding single or double quotes are stripped. Unquoted values
    that use command substitution (``$(...)`` or backticks) are skipped,
    since pyautoenv2 never evaluates shell. Quoted values are taken
    verbatim.
    """
    value = raw.strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if "$(" in value or "`" in value:
        return None
    return value


def _envrc_load_command(envrc_dir: str, base_env: Dict[str, str]) -> str:
    """Return the command exporting the variables in ``envrc_dir``'s file."""
    pairs = parse_envrc(os.path.join(envrc_dir, ENVRC_NAME))
    if not pairs:
        return ""
    names = [name for name, _ in pairs]
    backup = [(name, base_env[name]) for name in names if name in base_env]
    encoded_backup = _encode_envrc_backup(backup)
    cmds = [f"export {name}={_sh_quote(value)}" for name, value in pairs]
    cmds.append(f"export {ENVRC_DIR}={_sh_quote(envrc_dir)}")
    cmds.append(f"export {ENVRC_VARS}={_sh_quote(';'.join(names))}")
    cmds.append(f"export {ENVRC_BACKUP}={_sh_quote(encoded_backup)}")
    return "; ".join(cmds)


def _envrc_unload_command() -> str:
    """Return the command that reverts the currently loaded '.envrc'."""
    names = [n for n in os.environ.get(ENVRC_VARS, "").split(";") if n]
    backup = _decode_envrc_backup(os.environ.get(ENVRC_BACKUP, ""))
    cmds = []
    for name in names:
        if name in backup:
            cmds.append(f"export {name}={_sh_quote(backup[name])}")
        else:
            cmds.append(f"unset {name}")
    cmds.append(f"unset {ENVRC_DIR} {ENVRC_VARS} {ENVRC_BACKUP}")
    return "; ".join(cmds)


def _env_after_unload() -> Dict[str, str]:
    """Return this process's environment as it will be after an unload."""
    env = dict(os.environ)
    names = [n for n in env.get(ENVRC_VARS, "").split(";") if n]
    backup = _decode_envrc_backup(env.get(ENVRC_BACKUP, ""))
    for name in names:
        if name in backup:
            env[name] = backup[name]
        else:
            env.pop(name, None)
    for key in (ENVRC_DIR, ENVRC_VARS, ENVRC_BACKUP):
        env.pop(key, None)
    return env


def _same_envrc_dir(a: Union[str, None], b: Union[str, None]) -> bool:
    """Return True if the two paths refer to the same '.envrc' directory."""
    if a is None or b is None:
        return a is None and b is None
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return os.path.normpath(a) == os.path.normpath(b)


def _encode_envrc_backup(pairs: List[Tuple[str, str]]) -> str:
    """Encode previous variable values for safe storage in an env var."""
    import base64

    raw = "\n".join(f"{name}={value}" for name, value in pairs)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_envrc_backup(encoded: str) -> Dict[str, str]:
    """Decode the value produced by :func:`_encode_envrc_backup`."""
    if not encoded:
        return {}
    import base64

    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return {}
    backup: Dict[str, str] = {}
    for line in raw.split("\n"):
        if not line:
            continue
        name, _, value = line.partition("=")
        backup[name] = value
    return backup


def get_virtual_env(args: Args) -> Union[str, None]:
    """Return the activator for the venv if defined in the given directory."""
    venv_dir = venv_activator(args)
    if venv_dir:
        return venv_dir
    if has_poetry_env(args.directory):
        return poetry_activator(args)
    return None


def venv_activator(args: Args) -> Union[str, None]:
    """
    Return the venv activator within the given directory.

    Return None if the directory does not contain a venv, or the venv
    does not contain a suitable activator script.
    """
    for path in venv_candidate_dirs(args):
        for activate_script in iter_candidate_activators(path, args):
            if __debug__:
                logger.debug("venv_activator: candidate '%s'", activate_script)
            if os.path.isfile(activate_script):
                return activate_script
    return None


def venv_candidate_dirs(args: Args) -> Iterator[str]:
    """Get candidate venv paths within the given directory."""
    for venv_name in venv_dir_names():
        yield os.path.join(args.directory, venv_name)


@lru_cache(maxsize=1)
def venv_dir_names() -> List[str]:
    """Get the possible names for a venv directory."""
    name_list = os.environ.get(VENV_NAMES)
    if name_list:
        return [x for x in name_list.split(";") if x]
    return [".venv"]


def has_poetry_env(directory: str) -> bool:
    """Return true if the given directory contains a poetry project."""
    return os.path.isfile(os.path.join(directory, "poetry.lock"))


def poetry_activator(args: Args) -> Union[str, None]:
    """
    Return the activator associated with a poetry project directory.

    If there are multiple poetry environments, pick the one with the
    latest modification time.
    """
    env_list = poetry_env_list(args.directory)
    if env_list:
        env_dir = max(env_list, key=lambda p: os.stat(p).st_mtime)
        for env_activator in iter_candidate_activators(env_dir, args):
            if __debug__:
                logger.debug(
                    "poetry_activator: candidate: '%s'", env_activator
                )
            if os.path.isfile(env_activator):
                return env_activator
    return None


def poetry_env_list(directory: str) -> List[str]:
    """
    Return list of poetry environments for the given directory.

    This can be found via the poetry CLI using
    ``poetry env list --full-path``, but it's painfully slow.
    """
    cache_dir = poetry_cache_dir()
    if cache_dir is None:
        return []
    env_name = poetry_env_name(directory)
    if __debug__:
        logger.debug("poetry_env_list: env name: '%s'", env_name)
    if env_name is None:
        return []
    virtual_env_path = os.path.join(cache_dir, "virtualenvs")
    if __debug__:
        logger.debug("poetry_env_list: venvs path: '%s'", virtual_env_path)
    try:
        return [
            f.path
            for f in os.scandir(virtual_env_path)
            if f.name.startswith(f"{env_name}-py")
        ]
    except OSError:
        if __debug__:
            logger.debug("poetry_env_list: os error:")
            logger.exception("")
        return []


@lru_cache(maxsize=1)
def poetry_cache_dir() -> Union[str, None]:
    """Return the poetry cache directory, or None if it's not found."""
    cache_dir = os.environ.get("POETRY_CACHE_DIR")
    if cache_dir and os.path.isdir(cache_dir):
        return cache_dir
    op_sys = operating_system()
    if op_sys == OS_WINDOWS:
        return windows_poetry_cache_dir()
    if op_sys == OS_MACOS:
        return macos_poetry_cache_dir()
    if op_sys == OS_LINUX:
        return linux_poetry_cache_dir()
    return None


def linux_poetry_cache_dir() -> Union[str, None]:
    """Return the poetry cache directory for Linux."""
    xdg_cache = os.environ.get(
        "XDG_CACHE_HOME",
        os.path.join(os.path.expanduser("~"), ".cache"),
    )
    return os.path.join(xdg_cache, "pypoetry")


def macos_poetry_cache_dir() -> str:
    """Return the poetry cache directory for MacOS."""
    return os.path.join(
        os.path.expanduser("~"),
        "Library",
        "Caches",
        "pypoetry",
    )


def windows_poetry_cache_dir() -> Union[str, None]:
    """Return the poetry cache directory for Windows."""
    app_data = os.environ.get("LOCALAPPDATA", None)
    if not app_data:
        return None
    return os.path.join(app_data, "pypoetry", "Cache")


def poetry_env_name(directory: str) -> Union[str, None]:
    """
    Get the name of the poetry environment defined in the given directory.

    A poetry environment directory will have a name of the form
    ``pyautoenv2-AacnJhVq-py3.10``. Where the first part is the
    (sanitized) project name taken from 'pyproject.toml'. The second
    part is the first 8 characters of the (base64 encoded) SHA256 hash
    of the absolute path of the project directory. The final part is
    'py' followed by the Python version (<major>.<minor>).

    This function derives the first two parts of this name. There may be
    multiple environments (using different Python versions) for a given
    poetry project, so we must search for the final part of the name
    later.

    Logic comes from the poetry source code:
    https://github.com/python-poetry/poetry/blob/2b15ce10f02b0c6347fe2f12ae902488edeaaf7c/src/poetry/utils/env.py#L1207.
    """
    name = poetry_project_name(directory)
    if name is None:
        return None

    # These two take roughly the same amount of time to import as it
    # does to run the rest of the script. Import locally here, so we're
    # only importing when we know that we need to.
    import base64
    import hashlib

    sanitized_name = (
        # This is a bit ugly, but it's more performant than using a regex.
        # The import time for the 're' module is also a factor.
        name.replace(" ", "_")
        .replace("$", "_")
        .replace("`", "_")
        .replace("!", "_")
        .replace("*", "_")
        .replace("@", "_")
        .replace("\\", "_")
        .replace("\r", "_")
        .replace("\n", "_")
        .replace("\t", "_")
        .lower()[:42]
    )
    normalized_path = os.path.normcase(directory)
    path_hash = hashlib.sha256(normalized_path.encode()).digest()
    b64_hash = base64.urlsafe_b64encode(path_hash).decode()[:8]
    return f"{sanitized_name}-{b64_hash}"


def poetry_project_name(directory: str) -> Union[str, None]:
    """Parse the poetry project name from the given directory."""
    pyproject_file_path = os.path.join(directory, "pyproject.toml")
    try:
        with open(pyproject_file_path, encoding="utf-8") as pyproject_file:
            return parse_name_from_pyproject_file(pyproject_file)
    except OSError:
        return None


def parse_name_from_pyproject_file(file: TextIO) -> Union[str, None]:
    """
    Parse the project name from a pyproject.toml file.

    Return ``None`` if the name cannot be parsed.
    """
    # Ideally we'd use a proper TOML parser to do this, but there isn't
    # one available in the standard library until Python 3.11. This
    # hacked together parser should work for the vast majority of cases.
    for line in file:
        line = line.strip()  # noqa: PLW2901
        if line in ("[project]", "[tool.poetry]"):
            for project_line in file:
                project_line = project_line.lstrip().lstrip("'\"")  # noqa: PLW2901
                if project_line.startswith("["):
                    # New block started without finding the project name.
                    return None
                if not project_line.startswith("name"):
                    continue
                try:
                    key, val = project_line.split("=", maxsplit=1)
                except ValueError:
                    continue
                if key.rstrip().rstrip("'\"") == "name":
                    return val.strip().strip("'\"")
    return None


def iter_candidate_activators(env_directory: str, args: Args) -> Iterator[str]:
    """
    Iterate over candidate activator paths.

    In general we'll know exactly the activator we want given the
    environment directory and the shell we're using. However, in some
    cases there may be slightly different activator script names
    depending on how the venv was created.
    """
    bin_dir = "Scripts" if operating_system() == OS_WINDOWS else "bin"
    if args.fish:
        script = "activate.fish"
    elif args.pwsh:
        # PowerShell activation scripts on *Nix systems have some
        # slightly inconsistent naming. When using Poetry or uv, the
        # activation script is lower case, using the venv module,
        # the script is title case.
        for script in ("activate.ps1", "Activate.ps1"):
            script_path = os.path.join(env_directory, bin_dir, script)
            yield script_path
        return
    else:
        script = "activate"
    yield os.path.join(env_directory, bin_dir, script)


def is_local_venv_activator(activator_path: str) -> bool:
    """Return True if the activator is for a project-local venv."""
    venv_dir = activator_venv_dir(activator_path)
    return os.path.basename(venv_dir) in venv_dir_names()


def venv_is_relocated(venv_dir: str) -> bool:
    """Return True if the venv's recorded path doesn't match its real path."""
    actual = os.path.realpath(venv_dir)
    for recorded in (
        recorded_virtual_env(venv_dir),
        recorded_pyvenv_cfg_path(venv_dir),
    ):
        if recorded and os.path.realpath(recorded) != actual:
            return True
    return False


def recorded_pyvenv_cfg_path(venv_dir: str) -> Union[str, None]:
    """Read the venv path baked into pyvenv.cfg's ``command`` line.

    pyvenv.cfg looks like::

        command = /usr/bin/python3.12 -m venv /path/to/.venv

    The last whitespace-separated token is the original venv path.
    """
    cfg_path = os.path.join(venv_dir, "pyvenv.cfg")
    try:
        with open(cfg_path, encoding="utf-8") as cfg_file:
            for raw_line in cfg_file:
                line = raw_line.strip()
                if not line.startswith("command"):
                    continue
                _, _, value = line.partition("=")
                tokens = value.strip().split()
                if tokens:
                    return tokens[-1]
    except OSError:
        return None
    return None


def recorded_virtual_env(venv_dir: str) -> Union[str, None]:
    """Read the VIRTUAL_ENV path baked into the venv's activate script.

    Modern activate scripts (e.g. those produced by uv) emit two
    assignments: a cygwin-only branch using ``$(cygpath ...)`` and a
    plain-literal branch for everything else. We want the literal one.
    """
    bin_dir = "Scripts" if operating_system() == OS_WINDOWS else "bin"
    activate_path = os.path.join(venv_dir, bin_dir, "activate")
    try:
        with open(activate_path, encoding="utf-8") as activate_file:
            for raw_line in activate_file:
                line = raw_line.strip()
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                if not line.startswith("VIRTUAL_ENV="):
                    continue
                val = line.split("=", 1)[1].strip()
                if not val or val.startswith(("$(", "`")):
                    continue
                if len(val) > 1 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                return val
    except OSError:
        return None
    return None


_VIRTUAL_ENV_LINE_PATTERNS_QUOTED = (
    # POSIX shell: [export ]VIRTUAL_ENV='...' / "..."
    re.compile(r"^(\s*(?:export\s+)?VIRTUAL_ENV=)(['\"])([^'\"$`\n]*)(['\"])(\s*)$"),
    # fish: set -gx VIRTUAL_ENV "..."
    re.compile(r"^(\s*set\s+-gx\s+VIRTUAL_ENV\s+)(['\"])([^'\"$`\n]*)(['\"])(\s*)$"),
    # csh: setenv VIRTUAL_ENV "..."
    re.compile(r"^(\s*setenv\s+VIRTUAL_ENV\s+)(['\"])([^'\"$`\n]*)(['\"])(\s*)$"),
    # PowerShell: $env:VIRTUAL_ENV = "..."
    re.compile(r"^(\s*\$env:VIRTUAL_ENV\s*=\s*)(['\"])([^'\"$`\n]*)(['\"])(\s*)$"),
)

# Unquoted absolute-path assignments. Only match when the value starts with
# '/' (POSIX) or a Windows drive letter, and contains no shell metacharacters.
_VIRTUAL_ENV_LINE_PATTERNS_UNQUOTED = (
    # POSIX: VIRTUAL_ENV=/abs/path
    re.compile(r"^(\s*(?:export\s+)?VIRTUAL_ENV=)(/[^'\"$`\s\n]*)(\s*)$"),
    # fish: set -gx VIRTUAL_ENV /abs/path
    re.compile(r"^(\s*set\s+-gx\s+VIRTUAL_ENV\s+)(/[^'\"$`\s\n]*)(\s*)$"),
    # csh: setenv VIRTUAL_ENV /abs/path
    re.compile(r"^(\s*setenv\s+VIRTUAL_ENV\s+)(/[^'\"$`\s\n]*)(\s*)$"),
)


def _rewrite_virtual_env_line(line: str, venv_dir: str) -> Union[str, None]:
    """Return a rewritten line, or None if it doesn't assign VIRTUAL_ENV."""
    for pat in _VIRTUAL_ENV_LINE_PATTERNS_QUOTED:
        m = pat.match(line)
        if m:
            return (
                f"{m.group(1)}{m.group(2)}{venv_dir}"
                f"{m.group(4)}{m.group(5)}"
            )
    for pat in _VIRTUAL_ENV_LINE_PATTERNS_UNQUOTED:
        m = pat.match(line)
        if m:
            return f"{m.group(1)}{venv_dir}{m.group(3)}"
    return None


def _rewrite_activate_file(path: str, venv_dir: str) -> None:
    """Rewrite literal VIRTUAL_ENV assignments in a single activate script."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return
    changed = False
    for i, line in enumerate(lines):
        new = _rewrite_virtual_env_line(line, venv_dir)
        if new is None:
            continue
        if not new.endswith("\n") and line.endswith("\n"):
            new += "\n"
        if new != line:
            lines[i] = new
            changed = True
    if not changed:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError:
        if __debug__:
            logger.warning(
                "repair_venv_paths: failed to write '%s'", path,
            )


def _run_repair(rest: List[str]) -> int:
    """Handle the ``--repair`` CLI flag."""
    if not rest:
        return 1
    repair_venv_paths(rest[0])
    return 0


def repair_venv_paths(venv_dir: str) -> None:
    """Rewrite VIRTUAL_ENV references in activate scripts after relocation."""
    venv_dir = os.path.realpath(venv_dir)
    for sub in ("bin", "Scripts"):
        bin_dir = os.path.join(venv_dir, sub)
        if not os.path.isdir(bin_dir):
            continue
        try:
            entries = os.listdir(bin_dir)
        except OSError:
            continue
        for name in entries:
            if not name.lower().startswith("activate"):
                continue
            path = os.path.join(bin_dir, name)
            if os.path.isfile(path):
                _rewrite_activate_file(path, venv_dir)


def is_relocation_dismissed(venv_dir: str) -> bool:
    """Return True if the user already declined to fix this venv."""
    raw = os.environ.get(DISMISSED_RELOCATIONS, "")
    if not raw:
        return False
    target = os.path.realpath(venv_dir)
    return any(
        os.path.realpath(entry) == target for entry in raw.split(";") if entry
    )


def emit_relocation_prompt(
    stream: TextIO,
    venv_dir: str,
    activator: str,
    args: Args,
) -> None:
    """Write shell code that asks the user whether to repair a moved venv."""
    recorded = recorded_virtual_env(venv_dir) or "<unknown>"
    message = (
        f"pyautoenv2: venv at {venv_dir} appears to have been moved "
        f"(recorded path: {recorded}). Repair it? [y/N] "
    )
    if __debug__:
        logger.debug("emit_relocation_prompt: '%s'", venv_dir)
    if args.fish:
        cmd = _fish_relocation_command(message, venv_dir, activator)
    elif args.pwsh:
        cmd = _pwsh_relocation_command(message, venv_dir, activator)
    else:
        cmd = _posix_relocation_command(message, venv_dir, activator)
    stream.write(cmd)


def _script_path() -> str:
    """Return the absolute path of pyautoenv2.py for shell call-backs."""
    candidate = sys.argv[0] if sys.argv and sys.argv[0] else __file__
    try:
        return os.path.realpath(candidate)
    except OSError:
        return os.path.realpath(__file__)


def _posix_relocation_command(
    message: str, venv_dir: str, activator: str,
) -> str:
    msg_q = _sh_quote(message)
    venv_q = _sh_quote(venv_dir)
    act_q = _sh_quote(activator)
    script_q = _sh_quote(_script_path())
    return (
        f"_pae_venv={venv_q}; "
        f"printf '%s' {msg_q}; "
        "if IFS= read -r _pae_reply </dev/tty 2>/dev/null; then :; "
        "else _pae_reply=; fi; "
        "case \"$_pae_reply\" in "
        "[Yy]*) "
        "{ [ -n \"${VIRTUAL_ENV-}\" ] && deactivate; } 2>/dev/null; "
        "find \"$_pae_venv/bin\" -maxdepth 1 -xtype l -delete 2>/dev/null; "
        f"python3 -m venv --upgrade \"$_pae_venv\" && "
        f"python3 {script_q} --repair \"$_pae_venv\" && . {act_q} ;; "
        "*) "
        "_pae_dr=\"${PYAUTOENV_DISMISSED_RELOCATIONS-}\"; "
        "if [ -n \"$_pae_dr\" ]; then "
        "_pae_dr=\"$_pae_dr;$_pae_venv\"; "
        "else "
        "_pae_dr=\"$_pae_venv\"; "
        "fi; "
        "export PYAUTOENV_DISMISSED_RELOCATIONS=\"$_pae_dr\"; "
        "unset _pae_dr ;; "
        "esac; "
        "unset _pae_reply _pae_venv"
    )


def _fish_relocation_command(
    message: str, venv_dir: str, activator: str,
) -> str:
    msg_q = _sh_quote(message)
    venv_q = _sh_quote(venv_dir)
    act_q = _sh_quote(activator)
    script_q = _sh_quote(_script_path())
    return (
        f"set -l _pae_venv {venv_q}; "
        f"read -l -P {msg_q} _pae_reply; "
        "if string match -qr '^[Yy]' -- $_pae_reply; "
        "if set -q VIRTUAL_ENV; deactivate; end; "
        "find \"$_pae_venv/bin\" -maxdepth 1 -xtype l -delete 2>/dev/null; "
        "python3 -m venv --upgrade $_pae_venv; "
        f"and python3 {script_q} --repair $_pae_venv; "
        f"and . {act_q}; "
        "else; "
        "set -gx PYAUTOENV_DISMISSED_RELOCATIONS "
        "(string join ';' -- $PYAUTOENV_DISMISSED_RELOCATIONS $_pae_venv); "
        "end; "
        "set -e _pae_reply; set -e _pae_venv"
    )


def _pwsh_relocation_command(
    message: str, venv_dir: str, activator: str,
) -> str:
    msg_q = _pwsh_quote(message)
    venv_q = _pwsh_quote(venv_dir)
    act_q = _pwsh_quote(activator)
    script_q = _pwsh_quote(_script_path())
    return (
        f"$_pae_venv = {venv_q}; "
        f"$_pae_reply = Read-Host -Prompt {msg_q}; "
        "if ($_pae_reply -match '^[Yy]') { "
        "if ($env:VIRTUAL_ENV) { deactivate }; "
        "& python3 -m venv --upgrade $_pae_venv; "
        f"if ($?) {{ & python3 {script_q} --repair $_pae_venv; . {act_q} }} "
        "} else { "
        "if ($env:PYAUTOENV_DISMISSED_RELOCATIONS) { "
        "$env:PYAUTOENV_DISMISSED_RELOCATIONS = "
        "$env:PYAUTOENV_DISMISSED_RELOCATIONS + ';' + $_pae_venv "
        "} else { "
        "$env:PYAUTOENV_DISMISSED_RELOCATIONS = $_pae_venv "
        "} "
        "}; "
        "Remove-Variable _pae_reply,_pae_venv -ErrorAction SilentlyContinue"
    )


def _sh_quote(value: str) -> str:
    """Return value quoted for POSIX shells."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _pwsh_quote(value: str) -> str:
    """Return value quoted as a PowerShell single-quoted string."""
    return "'" + value.replace("'", "''") + "'"


@lru_cache(maxsize=1)
def operating_system() -> Union[int, None]:
    """
    Return the operating system the script's being run on.

    Return 'None' if we're on an operating system we can't handle.
    """
    if sys.platform.startswith("darwin"):
        return OS_MACOS
    if sys.platform.startswith("win"):
        return OS_WINDOWS
    if sys.platform.startswith("linux"):
        return OS_LINUX
    return None


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:], sys.stdout))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"pyautoenv2: error: {exc}\n")
        if __debug__:
            logger.exception("backtrace:")
        sys.exit(1)
