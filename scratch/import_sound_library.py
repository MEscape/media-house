"""Import the owner's sound library into an app home and measure every sound (cached).

    uv run python scratch/import_sound_library.py
    uv run python scratch/import_sound_library.py --manifest D:/Sounds/manifest.json --home ...

Use the same ``--home`` as ``scratch/run_pipeline.py``: the ``sound_design`` stage chooses music
and effects only from the sounds stored there. Idempotent: identical files are stored once and a
sound whose measurement is stored is not measured again (a second run reports everything reused).
Licence facts come from the manifest; sounds whose commercial use is not confirmed are imported
but never selected. Exit codes: 0 ok; 1 a file could not be imported or measured; 2 bad arguments.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.music_and_sound_design.application.contracts import (
    ImportSoundLibraryCommand,
    SoundLibraryImporter,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err

DEFAULT_MANIFEST = Path.home() / "Documents" / "MediaHouseAssets" / "manifest.json"
DEFAULT_HOME = Path.home() / ".media_house_scratch" / "pipeline"


def main(argv: Sequence[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description="Import and measure the sound library.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME, help="application home")
    parser.add_argument(
        "--no-analysis", action="store_true", help="import only; measure nothing (not advised)"
    )
    args = parser.parse_args(argv)
    if not args.manifest.is_file():
        parser.error(f"no manifest at {args.manifest}")

    app = Application.start(
        StartupOptions(
            env={},
            home=args.home,
            environment=Environment.DEVELOPMENT,
            shared_installs=True,  # models and HyperFrames are installed once per machine
        )
    )
    try:
        print(
            f"Importing {args.manifest} into {args.home} (measuring takes minutes the first time)"
        )
        result = app.container.resolve(SoundLibraryImporter).execute(
            ImportSoundLibraryCommand(args.manifest, analyze=not args.no_analysis),
            JobContext.detached(),
        )
        if isinstance(result, Err):
            print(f"x {result.error.user_message}", file=sys.stderr)
            return 1
        report = result.value
        new = sum(s.created for s in report.sounds)
        print(
            f"  sounds: {len(report.sounds)} ({new} new, {len(report.sounds) - new} already stored)"
        )
        print(
            f"  measured: {len(report.analysis.analysed)} now, {len(report.analysis.reused)} reused"
        )
        print(f"  not cleared for commercial use (never selected): {len(report.not_cleared)}")
        print(f"  no AI/test use allowed: {report.no_training}")
        for path, first in report.duplicates:
            print(f"  duplicate: {path} = {first}")
        for path in report.checksum_mismatches:
            print(f"  ! checksum differs from the manifest: {path}")
        problems = [*report.failed, *report.analysis.failed]
        for what, reason in problems:
            print(f"  x {what}: {reason}")
        return 1 if problems else 0
    finally:
        app.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
