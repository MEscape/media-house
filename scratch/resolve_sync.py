"""Read the fixes a person made in DaVinci Resolve back into Media House.

    uv run python scratch/resolve_sync.py --home H --anchor <recording asset id>
    uv run python scratch/resolve_sync.py --home H --anchor <id> --look-only

Compares the Resolve timeline with what was last in sync (move, trim, disable, opacity, delete)
and hands the changes to the timeline builder as a person's edits, so the next pipeline run and the
next ``--stages resolve_execute`` keep them instead of rebuilding over them. ``--look-only``
reports without recording. Resolve must be running; see ``scratch/run_pipeline.py`` for where the
scripting library is looked for (``--resolve-lib`` / ``--resolve-api``).

Exit codes: 0 in sync or recorded; 1 something was refused or Resolve was not reachable.
"""

import argparse
import sys
from pathlib import Path

from media_house.bootstrap.application import Application, StartupOptions
from media_house.bootstrap.modules import installed_modules
from media_house.modules.resolve_adapter.application.contracts import (
    ResolveSynchronizer,
    SyncCommand,
)
from media_house.modules.resolve_adapter.infrastructure.install import ResolveInstall
from media_house.modules.resolve_adapter.module import ResolveAdapterModule
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument("--home", type=Path, default=Path.home() / ".media_house_scratch/pipeline")
    parser.add_argument("--anchor", required=True, help="the recording's asset id")
    parser.add_argument("--look-only", action="store_true", help="report, record nothing")
    parser.add_argument("--resolve-lib", type=Path, help="Resolve's fusionscript library")
    parser.add_argument("--resolve-api", type=Path, help="Resolve's Developer/Scripting folder")
    args = parser.parse_args()
    overrides = {
        **({"RESOLVE_SCRIPT_LIB": str(args.resolve_lib)} if args.resolve_lib else {}),
        **({"RESOLVE_SCRIPT_API": str(args.resolve_api)} if args.resolve_api else {}),
    }
    install = ResolveInstall.defaults(sys.platform, overrides)
    modules = [
        *(m for m in installed_modules() if m.name != "resolve_adapter"),
        ResolveAdapterModule(install=install),
    ]
    app = Application.start(
        StartupOptions(env={}, home=args.home, environment=Environment.DEVELOPMENT, modules=modules)
    )
    try:
        result = app.container.resolve(ResolveSynchronizer).execute(
            SyncCommand(anchor_asset_id=args.anchor, record=not args.look_only),
            JobContext.detached(),
        )
        if isinstance(result, Err):
            print(f"x {result.error.user_message}")
            return 1
        sync = result.value.report.sync
        print(f"status: {sync.status}")
        for label, ids in (
            ("changed by a person", sync.human_modified),
            ("deleted by a person", sync.human_deleted),
            ("added by a person (left alone)", sync.human_created),
            ("recorded in Media House", sync.recorded),
        ):
            if ids:
                print(f"  {label}: {', '.join(ids)}")
        for item in result.value.unrecorded:
            print(f"  stays in Resolve only: {item}")
        for error in result.value.report.errors:
            print(f"  x {error}")
        return 1 if result.value.report.errors else 0
    finally:
        app.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
