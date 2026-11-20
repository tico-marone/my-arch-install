"""Command-line entrypoint for my-arch-install."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import __version__
from .catalog import load_catalog
from .system_probe import discover_disks, run_preflight


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="my-arch-install",
        description="Personal, interactive Arch Linux TUI installer.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate commands without formatting disks or writing to /mnt.",
    )
    parser.add_argument(
        "--theme",
        choices=("auto", "ansi-dark", "textual-dark"),
        default="auto",
        help="TUI color theme (auto uses ansi-dark on Linux TTY).",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=Path("/tmp/my-arch-install.log"),
        help="Path for the temporary installation log in RAM (default: /tmp/my-arch-install.log).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run non-interactive preflight diagnostics and exit.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def run_check_mode() -> int:
    status = run_preflight()
    disks = discover_disks()
    catalog = load_catalog()

    print(f"my-arch-install {__version__} preflight check")
    print(f"  UEFI Boot        : {status.is_uefi} ({status.uefi_bitness})")
    print(f"  Internet         : {status.has_internet}")
    print(f"  NTP Synced       : {status.time_synced}")
    print(f"  reflector.svc    : {'active' if status.reflector_service_active else 'idle'}")
    print(f"  Missing Tools    : {', '.join(status.missing_tools) if status.missing_tools else 'none'}")
    print(f"  Candidate Disks  : {len(disks)}")
    for d in disks:
        print(f"    - {d.label}")
    print(f"  Catalog Packages : {len(catalog.packages)} ({len(catalog.required_names)} required)")
    return 0 if status.can_install else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check:
        return run_check_mode()

    try:
        from .tui.app import MyArchInstallApp
    except ImportError as exc:
        print(
            "Error: 'textual' is required for the TUI interface.\n"
            "On Arch Linux, install 'python-textual' (preinstalled on the official Live ISO).",
            file=sys.stderr,
        )
        print(f"Details: {exc}", file=sys.stderr)
        return 1

    app = MyArchInstallApp(
        dry_run=args.dry_run,
        log_path=args.log_path,
        theme_preference=args.theme,
    )
    app.run()
    return 0
