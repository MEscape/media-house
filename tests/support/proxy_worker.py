"""A separate OS process that talks to the same proxy database and library as the test.

Run as ``python -m tests.support.proxy_worker <command> --root ROOT ...``. It exists so the
cross-process tests exercise REAL processes (separate connections, separate clocks, a real hard
kill) instead of simulated owners. It uses the production wiring with a scripted encoder, so no
FFmpeg run is needed and the encode can be held, slowed or "crashed" deterministically through
files the test controls:

* ``--signal F``  created as soon as the worker is inside the encoder
* ``--release F`` the encoder blocks until this file exists
* ``--crash``     hard exit (``os._exit``) inside the encoder: no cleanup, like a killed process
* ``--ready F`` / ``--start F``  rendezvous so several workers start together

The last line of stdout is a JSON object describing the outcome.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from media_house.core.application.ports import ProcessRunner
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.core.infrastructure.system_clock import SystemClock
from media_house.core.modules import Container
from media_house.modules.media_inspection.module import MediaInspectionModule
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.modules.proxy_manager.application.commands import EnsureProxyCommand
from media_house.modules.proxy_manager.application.ensure_proxy import EnsureProxy
from media_house.modules.proxy_manager.application.ports import ProxyStore, Scratch
from media_house.modules.proxy_manager.module import ProxyManagerModule
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from tests.support.fakes import RecordingPublisher
from tests.support.proxy_fakes import CopyEncoder, FakeDisk, faithful_probe, source_facts

_WAIT_SECONDS = 60.0


def _container(root: Path) -> Container:
    from media_house.core.application.ports import Clock

    container = Container()
    container.register_instance(AppPaths, AppPaths.under_root(root / "app"))
    container.register_instance(Clock, SystemClock())
    container.register_instance(EventPublisher, RecordingPublisher())
    container.register_instance(ProcessRunner, SubprocessRunner())
    MediaLibraryModule().register(container)
    MediaInspectionModule().register(container)
    ProxyManagerModule().register(container)
    return container


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"{path.name} never appeared")
        time.sleep(0.02)


def _ensure(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root)
    container = _container(root)
    state = {"calls": 0}

    def hold(call: int) -> None:
        state["calls"] = call
        if args.signal:
            Path(args.signal).touch()
        if args.crash:
            os._exit(9)
        if args.sleep:
            time.sleep(args.sleep)
        if args.release:
            _wait_for(Path(args.release))

    encoder = CopyEncoder(root / "media" / "template.mp4", hold)
    probe = faithful_probe(encoder)
    probe.facts = source_facts()
    use_case = EnsureProxy(
        container.resolve(MediaLibrary),
        None,
        probe,
        encoder,
        container.resolve(ProxyStore),
        FakeDisk(),
        container.resolve(Scratch),
        SystemClock(),
        lease_seconds=args.lease,
        heartbeat_seconds=args.heartbeat,
        poll_seconds=args.poll,
        wait_timeout_seconds=args.wait_timeout,
    )
    if args.ready:
        Path(args.ready).touch()
    if args.start:
        _wait_for(Path(args.start))
    overrides = {"max_long_edge": args.edge} if args.edge else {}
    try:
        result = use_case.execute(
            EnsureProxyCommand(args.source, args.profile, overrides), JobContext.detached()
        )
    except BaseException as exc:  # noqa: BLE001 - reported to the test as data
        return {"status": "raised", "error": type(exc).__name__, "message": str(exc)}
    finally:
        calls = len(encoder.calls)
    if isinstance(result, Ok):
        return {
            "status": "ok",
            "proxy": result.value.proxy_asset_id,
            "reused": result.value.reused,
            "encoder_calls": calls,
        }
    assert isinstance(result, Err)
    return {
        "status": "err",
        "error": type(result.error).__name__,
        "message": str(result.error),
        "encoder_calls": calls,
    }


def _migrate(args: argparse.Namespace) -> dict[str, object]:
    container = _container(Path(args.root))
    if args.ready:
        Path(args.ready).touch()
    if args.start:
        _wait_for(Path(args.start))
    store = container.resolve(ProxyStore)
    store.get_slot("s", "edit")  # first use opens the database and migrates it
    return {"status": "ok"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("ensure", "migrate"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--source", default="")
    parser.add_argument("--profile", default="analysis")
    parser.add_argument("--edge", type=int, default=0)
    parser.add_argument("--signal", default="")
    parser.add_argument("--release", default="")
    parser.add_argument("--ready", default="")
    parser.add_argument("--start", default="")
    parser.add_argument("--crash", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--lease", type=float, default=120.0)
    parser.add_argument("--heartbeat", type=float, default=15.0)
    parser.add_argument("--poll", type=float, default=0.05)
    parser.add_argument("--wait-timeout", type=float, default=60.0)
    args = parser.parse_args()
    outcome = _ensure(args) if args.command == "ensure" else _migrate(args)
    sys.stdout.write(json.dumps(outcome) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
