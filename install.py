#!/usr/bin/env python3
"""Install pyautoenv2 for the current user."""

import argparse
import os
import shutil
import sys
from pathlib import Path

DEFAULT_INSTALL_DIR = Path.home() / ".local" / "share" / "pyautoenv2"

SHELL_CONFIGS = {
    "zsh": {
        "rc": Path.home() / ".zshrc",
        "source_file": "pyautoenv2.plugin.zsh",
        "source_line": 'source "{install_dir}/pyautoenv2.plugin.zsh"',
    },
    "bash": {
        "rc": Path.home() / ".bashrc",
        "source_file": "pyautoenv2.bash",
        "source_line": 'source "{install_dir}/pyautoenv2.bash"',
    },
    "fish": {
        "rc": Path.home() / ".config" / "fish" / "config.fish",
        "source_file": "pyautoenv2.fish",
        "source_line": 'source "{install_dir}/pyautoenv2.fish"',
    },
    "pwsh": {
        "rc": None,
        "source_file": "PyAutoEnv2.ps1",
        "source_line": '. "{install_dir}/PyAutoEnv2.ps1"',
    },
}

FILES_TO_COPY = [
    "pyautoenv2.py",
    "pyautoenv2.plugin.zsh",
    "pyautoenv2.bash",
    "pyautoenv2.fish",
    "PyAutoEnv2.ps1",
]


def detect_shell() -> str:
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return "zsh"
    if "bash" in shell:
        return "bash"
    if "fish" in shell:
        return "fish"
    return ""


def copy_files(src_dir: Path, install_dir: Path) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    for name in FILES_TO_COPY:
        src = src_dir / name
        if src.exists():
            shutil.copy2(src, install_dir / name)
            print(f"  copied {name}")


def add_source_line(rc: Path, line: str) -> bool:
    rc.parent.mkdir(parents=True, exist_ok=True)
    existing = rc.read_text() if rc.exists() else ""
    if line in existing:
        return False
    with rc.open("a") as f:
        f.write(f"\n# pyautoenv2\n{line}\n")
    return True


def install_shell(shell: str, install_dir: Path) -> None:
    config = SHELL_CONFIGS[shell]
    line = config["source_line"].format(install_dir=install_dir)
    rc: Path | None = config["rc"]

    if shell == "pwsh":
        print("\nAdd this line to your PowerShell profile ($PROFILE):")
        print(f"  {line}")
        return

    assert rc is not None
    if add_source_line(rc, line):
        print(f"  added source line to {rc}")
        print(f"  restart your shell or run: source {rc}")
    else:
        print(f"  {rc} already contains the source line — nothing to do")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install pyautoenv2")
    parser.add_argument(
        "--shell",
        choices=list(SHELL_CONFIGS),
        help="target shell (default: auto-detect)",
    )
    parser.add_argument(
        "--dir",
        metavar="PATH",
        help=f"install directory (default: {DEFAULT_INSTALL_DIR})",
    )
    args = parser.parse_args()

    install_dir = (
        Path(args.dir).expanduser().resolve()
        if args.dir
        else DEFAULT_INSTALL_DIR
    )

    shell = args.shell or detect_shell()
    if not shell:
        print(
            "Could not detect shell. Use --shell zsh|bash|fish|pwsh.",
            file=sys.stderr,
        )
        sys.exit(1)

    src_dir = Path(__file__).parent

    print(f"Installing pyautoenv2 to {install_dir} ...")
    copy_files(src_dir, install_dir)

    print(f"Configuring {shell} ...")
    install_shell(shell, install_dir)

    print("Done.")


if __name__ == "__main__":
    main()
