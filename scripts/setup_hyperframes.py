"""Install the pinned HyperFrames renderer for the motion graphics engine: the ONE download step.

The application never downloads while it renders; a missing renderer is reported with this
command. Run it once (and again after the pinned version in
``motion_graphics_engine/infrastructure/hyperframes_runtime/package.json`` changes):

    uv run python scripts/setup_hyperframes.py              # npm ci + pinned headless Chrome
    uv run python scripts/setup_hyperframes.py --check      # only report what is installed
    uv run python scripts/setup_hyperframes.py --print-dir

Everything lands in ``<cache dir>/motion_graphics/hyperframes`` (Windows:
``%LOCALAPPDATA%\\media-house\\Cache\\motion_graphics\\hyperframes``): the npm packages from the
lockfile (exact versions) and, under ``home/``, the Chrome build HyperFrames pins. TLS
verification stays on; Node additionally trusts the system certificate store
(``NODE_OPTIONS=--use-system-ca``), which is what TLS-inspecting proxies need.

Licences (commercial use allowed): HyperFrames Apache-2.0; GSAP "Standard no charge" license.
"""

import argparse
import sys

from media_house.core.infrastructure.process.subprocess_runner import SubprocessRunner
from media_house.modules.motion_graphics_engine.infrastructure.hyperframes import (
    LICENSES,
    install_dir,
    locate,
    pinned,
    setup,
)
from media_house.shared.filesystem import AppPaths


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="report only, download nothing")
    parser.add_argument("--print-dir", action="store_true", help="print the install folder")
    args = parser.parse_args(argv)
    target = install_dir(AppPaths.for_user())
    if args.print_dir:
        print(target)
        return 0
    versions = ", ".join(f"{name} {version}" for name, version in sorted(pinned().items()))
    print(f"pinned: {versions}")
    print(f"licences: {LICENSES}")
    if not args.check:
        problem = setup(SubprocessRunner(), target, lambda line: print(line, flush=True))
        if problem:
            print(f"setup failed: {problem}", file=sys.stderr)
            return 1
    found = locate(target)
    if isinstance(found, str):
        print(f"not available: {found}")
        return 1
    print(f"ready: hyperframes {found.version}\n  cli: {found.cli}\n  browser: {found.browser}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
