"""Command-line bootstrap: parse arguments, choose a mode, return an exit code.

python -m media_house               # launch the desktop application
python -m media_house --check       # start headless, verify, exit (CI / diagnostics)
python -m media_house --version
"""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from media_house import __version__
from media_house.bootstrap.application import Application, StartupOptions
from media_house.bootstrap.lifecycle import StartupError
from media_house.shared.configuration import Environment, LogLevel

EXIT_OK = 0
EXIT_STARTUP_FAILED = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="media-house", description="Media-House desktop application"
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument(
        "--check", action="store_true", help="start headless, verify the setup and exit"
    )
    parser.add_argument("--environment", choices=[e.value for e in Environment])
    parser.add_argument("--log-level", choices=[level.value for level in LogLevel])
    parser.add_argument(
        "--home", type=_path, help="keep all data under this directory (portable mode)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        print(f"media-house {__version__}")
        return EXIT_OK

    options = StartupOptions(
        env=dict(os.environ),
        environment=Environment(args.environment) if args.environment else None,
        log_level=LogLevel(args.log_level) if args.log_level else None,
        home=args.home,
    )
    if args.check:
        return run_check(options)

    # Imported lazily: Qt is only loaded when a window is actually needed.
    from media_house.bootstrap.desktop import run_desktop

    return run_desktop(options)


def run_check(options: StartupOptions) -> int:
    try:
        application = Application.start(options)
    except StartupError as error:
        print(format_startup_error(error), file=sys.stderr)
        return EXIT_STARTUP_FAILED
    try:
        print(f"media-house {__version__}: OK")
        print(f"  environment: {application.settings.environment.value}")
        print(f"  modules:     {', '.join(m.name for m in application.modules) or '(none)'}")
        print(f"  data dir:    {application.paths.data_dir}")
        print(f"  log dir:     {application.paths.log_dir}")
    finally:
        application.shutdown()
    return EXIT_OK


def format_startup_error(error: StartupError) -> str:
    return (
        f"Media-House could not start (phase: {error.phase.value}).\n"
        f"{error.user_message}\n"
        f"Reference: {error.error_id}\n"
        f"Technical cause: {type(error.__cause__).__name__}: {error.__cause__}"
    )


def _path(value: str) -> Path:
    return Path(value).expanduser()
