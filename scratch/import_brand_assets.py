"""Import the channel's brand pictures into an app home and name the slot each one fills.

    uv run python scratch/import_brand_assets.py --home H \
        --asset wolf_mascot=avatar.jpg --cutout wolf_mascot --asset fire_plate=fire_plate.png

Use the same ``--home`` as ``scratch/run_pipeline.py``, then pass the printed
``--brand-asset SLOT=ASSET_ID`` flags to it: the visual style binds each slot to that library asset
(``wolf_mascot`` and ``fire_plate`` are the required ones; ``visual_style_engine`` lists the rest).
``--cutout SLOT`` binds the slot to the transparent cut-out of the picture (background removed by
the library's own cut-out operation, model fetched with ``scripts/fetch_models.py
--background-removal birefnet-general-lite``) instead of the picture itself. Idempotent: identical
files are stored once and a cut-out that is stored is reused. Exit codes: 0 ok; 1 a file could not
be imported or cut out; 2 bad arguments.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.visual_asset_generation.application.contracts import (
    AdjustImageCommand,
    ImageAdjuster,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err

DEFAULT_HOME = Path.home() / ".media_house_scratch" / "pipeline"


def _slot_path(text: str) -> tuple[str, Path]:
    slot, sep, path = text.partition("=")
    if not sep or not slot or not path:
        raise argparse.ArgumentTypeError(f"expected SLOT=PATH, got {text!r}")
    return slot, Path(path)


def main(argv: Sequence[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description="Import the channel's brand assets.")
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME, help="application home")
    parser.add_argument(
        "--asset",
        type=_slot_path,
        action="append",
        default=[],
        metavar="SLOT=PATH",
        help="a picture for a brand slot (repeatable)",
    )
    parser.add_argument(
        "--cutout",
        action="append",
        default=[],
        metavar="SLOT",
        help="bind this slot to the picture's transparent cut-out (repeatable)",
    )
    args = parser.parse_args(argv)
    if not args.asset:
        parser.error("give at least one --asset SLOT=PATH")
    slots = {slot for slot, _ in args.asset}
    for slot in args.cutout:
        if slot not in slots:
            parser.error(f"--cutout {slot}: no --asset for that slot")
    for _, path in args.asset:
        if not path.is_file():
            parser.error(f"no such file: {path}")

    app = Application.start(
        StartupOptions(
            env={},
            home=args.home,
            environment=Environment.DEVELOPMENT,
            shared_installs=True,
        )
    )
    try:
        library = app.container.resolve(MediaLibrary)
        adjuster = app.container.resolve(ImageAdjuster)
        flags: list[str] = []
        failed = False
        for slot, path in args.asset:
            imported = library.import_file(path, display_name=f"brand {slot}", tags=("brand", slot))
            if isinstance(imported, Err):
                print(f"x {slot}: {imported.error.user_message}", file=sys.stderr)
                failed = True
                continue
            asset = imported.value.asset
            note = "new" if imported.value.created else "already stored"
            bound = asset.id
            if slot in args.cutout:
                cut = adjuster.execute(
                    AdjustImageCommand(source_asset_id=asset.id), JobContext.detached()
                )
                if isinstance(cut, Err):
                    print(f"x {slot}: cut-out failed: {cut.error.user_message}", file=sys.stderr)
                    failed = True
                    continue
                bound = cut.value.asset.id
                note += f", cut-out {'new' if cut.value.created else 'reused'}"
            print(f"  {slot}: {path.name} -> {bound} ({note})")
            flags.append(f"--brand-asset {slot}={bound}")
        if flags:
            print("pass to run_pipeline.py: " + " ".join(flags))
        return 1 if failed else 0
    finally:
        app.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
