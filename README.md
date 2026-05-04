# pyautoenv2

[![Build Status](https://github.com/hsaunders1904/pyautoenv/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/hsaunders1904/pyautoenv/actions/workflows/ci.yaml)
[![codecov](https://codecov.io/gh/hsaunders1904/pyautoenv/branch/main/graph/badge.svg?token=YABNBQOS1S)](https://codecov.io/gh/hsaunders1904/pyautoenv)

Automatically activate and deactivate Python environments
as you move around the file system.

## Description

Heavily inspired by [autoenv](https://github.com/hyperupcall/autoenv).
`pyautoenv2` activates a
[Poetry](https://python-poetry.org/) or
[venv](https://docs.python.org/3/library/venv.html)
Python environment when you cd into the directory that defines that environment
(i.e., when a directory, or any of its parents,
contains a `poetry.lock` file or a `.venv/` directory).
Environments are automatically deactivated when you leave the directory.

Supports Python versions 3.9 and up.

## Install

Follow the installation instructions for your favourite shell.

### Zsh

<details>
<summary>Expand instructions</summary>

If you're using [oh-my-zsh](https://ohmyz.sh/),
clone this repo into `~/.oh-my-zsh/plugins` or `${ZSH_CUSTOM}/plugins`.
Then add `pyautoenv2` to the list of enabled plugins in your `.zshrc`:

```zsh
plugins=(
    pyautoenv2
)
```

If you're not using `oh-my-zsh`, `source` the `pyautoenv2.plugin.zsh` script.

```zsh
source pyautoenv2.plugin.zsh
```

Add this to your `.zshrc` to activate the application permanently.

</details>

### Bash

<details>
<summary>Expand instructions</summary>

To enable the application in bash, source the bash script.

```bash
source <path to pyautoenv2>/pyautoenv2.bash
```

Add this to your `.bashrc` to activate the application permanently.

Note that this script will clobber the `cd` command.
It is highly recommended to use a more modern shell,
like ZSH or Fish, when using `pyautoenv2`.

</details>

### Fish

<details>
<summary>Expand instructions</summary>

To enable the application in fish-shell, source the fish script.

```fish
source <path to pyautoenv2>/pyautoenv2.fish
```

Add this to your `config.fish` file to activate the application permanently.

</details>

### PowerShell

<details>
<summary>Expand instructions</summary>

To enable the application in PowerShell, dot the `.ps1` file.

```pwsh
. <path to pyautoenv2>\PyAutoEnv2.ps1
```

Add this to your profile to activate the application permanently.

</details>

## Options

There are some environment variables you can set to configure `pyautoenv2`.

- `PYAUTOENV_DISABLE`: Set to a non-zero value to disable all functionality.
- `PYAUTOENV_VENV_NAME`:
  If you name your virtualenv directories something other than `.venv`,
  you can use this to override directory names to search within.
  Use `;` as a delimiter to separate directory names.
  For example, if set to `.venv;venv`, on each directory change,
  `pyautoenv2` will look for an environment within `.venv`,
  if that directory does not exist, it will look for an environment in `venv`.
- `PYAUTOENV_IGNORE_DIR`:
  If you wish to disable `pyautoenv2` for a specific set of directories,
  you can list these directories here,
  separated with a `;`.
  The directories, and their children,
  will be treated as though no virtual environment exists for them.
  This means any active environment will be deactivated when changing to them.
- `PYAUTOENV_DEBUG`: Set to a non-zero value to enable logging.
  When active, you can also use `PYAUTOENV_LOG_LEVEL`
  to set the logging level to something supported by Python's `logging` module.
  The default log level is `DEBUG`.
- `PYAUTOENV_DISMISSED_RELOCATIONS`:
  Populated by `pyautoenv2` itself when you decline to repair a relocated venv
  (see [Relocated venvs](#relocated-venvs) below).
  A `;`-separated list of venv paths that should be ignored for the rest of the
  shell session. You normally don't need to set this manually; clear it (or
  start a new shell) to be prompted again.

## Relocated venvs

If you move or rename a directory that contains a `.venv`, the activate
scripts and `pyvenv.cfg` inside it still reference the old absolute path,
which leaves the venv broken (the `VIRTUAL_ENV` and `PATH` set on activation
point at a directory that no longer exists).

When `pyautoenv2` enters a directory whose `.venv` has a recorded path that
doesn't match its current location, it prompts:

```
pyautoenv2: venv at /new/path/.venv appears to have been moved
(recorded path: /old/path/.venv). Repair it? [y/N]
```

- Answer `y` to repair the venv in-place via `python3 -m venv --upgrade`
  (rewrites the activate scripts and `pyvenv.cfg`; installed packages in
  `site-packages` are preserved) and activate it.
- Answer anything else to leave the venv alone. The path is added to
  `PYAUTOENV_DISMISSED_RELOCATIONS` so you won't be prompted again in this
  shell.

`pyautoenv2` is also resilient to the previously active venv being moved or
deleted: changing into a different directory will no longer crash because the
old `VIRTUAL_ENV` path is gone.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md).
