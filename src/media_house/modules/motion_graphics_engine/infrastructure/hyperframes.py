"""HyperFrames adapter: a pinned local install, run through ``ProcessRunner`` (never a shell).

Install layout (``AppPaths.cache_dir / "motion_graphics" / "hyperframes"``)::

    package.json, package-lock.json      copied from ``hyperframes_runtime/`` (package data)
    node_modules/hyperframes, gsap       ``npm ci`` from the lockfile (exact versions)
    home/                                HOME/USERPROFILE of every HyperFrames run: the pinned
                                         Chrome (``browser ensure``), font cache and CLI state
                                         live here, nothing in the user's profile

Downloads happen ONLY in ``setup`` (``scripts/setup_hyperframes.py``). A render runs offline:
telemetry, update checks, auto-install and skill installs are switched off by environment,
the browser is named explicitly and every asset is local.
"""

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.application.ports import (
    ComposedScene,
    Scene,
)
from media_house.modules.motion_graphics_engine.domain.engine import held_frames
from media_house.modules.motion_graphics_engine.domain.errors import RenderFailed
from media_house.modules.motion_graphics_engine.domain.profile import RenderSettings
from media_house.modules.motion_graphics_engine.infrastructure.composition import (
    GSAP_FILE,
    compose_html,
    image_file,
    scene_digest,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import (
    Err,
    Ok,
    ProcessTimeoutError,
    Result,
    ToolNotFoundError,
)
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

RUNTIME_DIR = Path(__file__).resolve().parent / "hyperframes_runtime"
SETUP_COMMAND = "uv run python scripts/setup_hyperframes.py"
#: Windows MAX_PATH: a longer executable path makes Chrome fail with a bare ENOENT.
_MAX_PATH = 260
#: Licences checked for commercial use (recorded in the README and the plan provenance).
LICENSES = {
    "hyperframes": "Apache-2.0",
    "gsap": "GreenSock Standard 'no charge' license (commercial use allowed)",
}


def install_dir(paths: AppPaths) -> Path:
    return paths.install("motion_graphics", "hyperframes")


def pinned() -> dict[str, str]:
    data = json.loads((RUNTIME_DIR / "package.json").read_text(encoding="utf-8"))
    deps: dict[str, str] = data["dependencies"]
    return deps


def run_env(install: Path, browser: Path | None) -> dict[str, str]:
    home = str(install / "home")
    env = {
        "HOME": home,
        "USERPROFILE": home,
        "HYPERFRAMES_NO_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "HYPERFRAMES_NO_UPDATE_CHECK": "1",
        "HYPERFRAMES_NO_AUTO_INSTALL": "1",
        "HYPERFRAMES_SKIP_SKILLS": "1",
        "HYPERFRAMES_FONT_CACHE_DIR": str(install / "home" / "fonts"),
    }
    if browser is not None:
        env["HYPERFRAMES_BROWSER_PATH"] = str(browser)
    return env


@dataclass(frozen=True, slots=True, kw_only=True)
class Runtime:
    cli: Path
    gsap: Path
    browser: Path
    version: str


def locate(install: Path) -> Runtime | str:
    """The installed runtime, or why it is missing (with the setup command)."""
    want = pinned()
    cli = install / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
    gsap = install / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    if not cli.is_file() or not gsap.is_file():
        return (
            f"HyperFrames {want['hyperframes']} is not installed in {install}; run: {SETUP_COMMAND}"
        )
    version = _version(install / "node_modules" / "hyperframes" / "package.json")
    if version != want["hyperframes"]:
        pin = want["hyperframes"]
        return f"HyperFrames {version} is installed, {pin} is pinned; run: {SETUP_COMMAND}"
    if _version(install / "node_modules" / "gsap" / "package.json") != want["gsap"]:
        return f"GSAP is not the pinned {want['gsap']}; run: {SETUP_COMMAND}"
    browsers = sorted(
        (install / "home" / ".cache" / "hyperframes" / "chrome").glob("**/chrome-headless-shell*")
    )
    browser = next((b for b in browsers if b.is_file() and b.suffix in (".exe", "")), None)
    if browser is None:
        return f"the pinned headless Chrome is not downloaded; run: {SETUP_COMMAND}"
    if os.name == "nt" and len(str(browser)) >= _MAX_PATH:
        return (
            f"the headless Chrome path is {len(str(browser))} characters, over the Windows limit "
            f"of {_MAX_PATH} (Chrome cannot start): install HyperFrames on a shorter path "
            f"({SETUP_COMMAND} installs into the user cache) or use a shorter --home"
        )
    if shutil.which("node") is None:
        return "Node.js (node) is not on PATH"
    return Runtime(cli=cli, gsap=gsap, browser=browser, version=version)


def _version(package_json: Path) -> str:
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
        return str(data["version"])
    except (OSError, ValueError, KeyError):
        return "missing"


def setup(runner: ProcessRunner, install: Path, report: Callable[[str], None]) -> str | None:
    """Install the pinned runtime (npm ci from the lockfile + the pinned Chrome).

    The ONLY place that downloads. TLS verification stays on: Node is told to also trust the
    system certificate store (``--use-system-ca``). Returns ``None`` or the problem.
    """
    install.mkdir(parents=True, exist_ok=True)
    (install / "home").mkdir(exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(RUNTIME_DIR / name, install / name)
    env = {"NODE_OPTIONS": "--use-system-ca", **run_env(install, None)}
    npm = shutil.which("npm")
    if npm is None:
        return "npm is not on PATH (install Node.js 22 or newer)"
    report("npm ci (pinned lockfile) ...")
    result = runner.run(
        ProcessSpec(
            npm, ("ci", "--no-audit", "--no-fund"), cwd=install, env=env, timeout_seconds=1800
        )
    )
    if not result.succeeded:
        return f"npm ci failed: {result.stderr[-2000:]}"
    report("hyperframes browser ensure (pinned headless Chrome) ...")
    cli = install / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
    result = runner.run(
        ProcessSpec(
            "node", (str(cli), "browser", "ensure"), cwd=install, env=env, timeout_seconds=1800
        )
    )
    if not result.succeeded:
        return f"browser ensure failed: {(result.stderr or result.stdout)[-2000:]}"
    found = locate(install)
    return found if isinstance(found, str) else None


#: HyperFrames' own MOV encode (``SDR_RGB_TO_TAGGED_BT709_FILTER`` of the pinned 0.9.5): the
#: still fast path must give the same pixels as a full render.
ENCODE_FILTER = (
    "scale=out_color_matrix=bt709:out_range=tv:flags=neighbor,"
    "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
)


class HyperFramesBackend:
    """``GraphicsBackend`` on a pinned HyperFrames install."""

    def __init__(self, runner: ProcessRunner, install: Path) -> None:
        self._runner = runner
        self._install = install

    def identity(self) -> tuple[str, str]:
        return "hyperframes", pinned()["hyperframes"]

    def unavailable(self) -> str | None:
        found = locate(self._install)
        return found if isinstance(found, str) else None

    def compose(self, scene: Scene) -> ComposedScene:
        document = compose_html(scene)
        files: list[tuple[str, Path]] = [(f"fonts/{f.key}.ttf", f.path) for f in scene.fonts]
        checks: list[tuple[str, str]] = [(f"fonts/{f.key}.ttf", f.sha256) for f in scene.fonts]
        for image in scene.images:
            files.append((image_file(image.asset_id), image.path))
            checks.append((image_file(image.asset_id), image.checksum))
        checks.append((GSAP_FILE, f"gsap@{pinned()['gsap']}"))
        still = scene.still_frame is not None
        return ComposedScene(
            document=document,
            files=tuple(files),
            digest=scene_digest(document, checks),
            width=round(scene.composition.width * scene.scale),
            height=round(scene.composition.height * scene.scale),
            frames=1 if still else scene.frames,
            still=still,
            samples=() if still else scene.samples,
        )

    def render(
        self,
        composed: ComposedScene,
        *,
        output: Path,
        fps: Rational,
        settings: RenderSettings,
        cancellation: CancellationToken,
    ) -> Result[Path, RenderFailed]:
        runtime = locate(self._install)
        if isinstance(runtime, str):
            return Err(RenderFailed(runtime))
        project = output.parent / f"{output.stem}-project"
        project.mkdir(parents=True, exist_ok=True)
        (project / "index.html").write_text(composed.document, encoding="utf-8")
        shutil.copyfile(runtime.gsap, project / GSAP_FILE)
        for name, source in composed.files:
            target = project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        sampled = bool(composed.samples) and not composed.still
        frames_dir = output.parent / f"{output.stem}-frames"
        target_path = frames_dir if composed.still or sampled else output
        args = [
            str(runtime.cli),
            "render",
            str(project),
            "--format",
            "png-sequence" if composed.still or sampled else "mov",
            "--fps",
            str(fps.numerator) if fps.denominator == 1 else f"{fps.numerator}/{fps.denominator}",
            "--output",
            str(target_path),
            "--workers",
            str(settings.workers),
            "--quiet",
            "--strict",
        ]
        if settings.software_gl:
            args.append("--no-browser-gpu")
        try:
            result = self._runner.run(
                ProcessSpec(
                    "node",
                    tuple(args),
                    cwd=project,
                    env=run_env(self._install, runtime.browser),
                    timeout_seconds=settings.timeout_seconds,
                ),
                cancellation=cancellation,
            )
        except (ToolNotFoundError, ProcessTimeoutError) as exc:
            return Err(RenderFailed(str(exc)))
        if not result.succeeded:
            tail = (result.stderr or result.stdout)[-1500:]
            _log.warning("HyperFrames render failed", exit_code=result.exit_code, tail=tail)
            return Err(RenderFailed(f"hyperframes exited with {result.exit_code}: {tail}"))
        if sampled:
            expanded = self._expand(composed, frames_dir, output, fps, settings, cancellation)
            if expanded is not None:
                return Err(expanded)
        if composed.still:
            pngs = sorted(target_path.glob("*.png"))
            if not pngs:
                return Err(RenderFailed("no frame was written"))
            shutil.copyfile(pngs[0], output)
        if not output.is_file():
            return Err(RenderFailed(f"{output.name} was not written"))
        return Ok(output)

    def _expand(
        self,
        composed: ComposedScene,
        frames_dir: Path,
        output: Path,
        fps: Rational,
        settings: RenderSettings,
        cancellation: CancellationToken,
    ) -> RenderFailed | None:
        """The drawn frames -> the full ProRes 4444 MOV, encoding each drawn frame ONCE.

        The drawn frames are encoded exactly as HyperFrames encodes its own MOV renders (same
        filter, codec, profile and pixel format), one single-frame MOV each. ProRes is
        intra-only, so a repeated frame is the same packet: the output is these files
        concatenated by stream copy, frame ``f`` taken from the drawn frame it repeats
        (``held_frames``). Exact frame count, no pixel re-encoded."""
        pngs = sorted(frames_dir.glob("*.png"))
        if len(pngs) != len(composed.samples):
            return RenderFailed(f"{len(pngs)} frames drawn, {len(composed.samples)} expected")
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            return RenderFailed("FFmpeg (ffmpeg) is not on PATH")
        for k, png in enumerate(pngs):
            png.replace(frames_dir / f"s{k:06d}.png")
        rate = str(fps.numerator) if fps.denominator == 1 else f"{fps.numerator}/{fps.denominator}"
        split = ",".join(str(k) for k in range(1, len(pngs)))
        encode = [
            "-v", "error", "-y", "-framerate", rate, "-i", str(frames_dir / "s%06d.png"),
            "-vf", ENCODE_FILTER, "-c:v", "prores_ks", "-profile:v", "4444",
            "-vendor", "apl0", "-pix_fmt", "yuva444p10le", "-an",
            *(["-f", "segment", "-segment_frames", split] if split else ["-f", "segment"]),
            "-reset_timestamps", "1", str(frames_dir / "u%06d.mov"),
        ]  # fmt: skip
        listing = frames_dir / "frames.txt"
        shown = held_frames(composed.samples, composed.frames)
        listing.write_text("".join(f"file 'u{k:06d}.mov'\n" for k in shown), encoding="utf-8")
        join = [
            "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", "-frames:v", str(composed.frames), str(output),
        ]  # fmt: skip
        for args in (encode, join):
            try:
                spec = ProcessSpec(
                    ffmpeg, tuple(args), cwd=frames_dir, timeout_seconds=settings.timeout_seconds
                )
                result = self._runner.run(spec, cancellation=cancellation)
            except (ToolNotFoundError, ProcessTimeoutError) as exc:
                return RenderFailed(str(exc))
            if not result.succeeded:
                tail = result.stderr[-1500:]
                return RenderFailed(f"ffmpeg exited with {result.exit_code}: {tail}")
        return None
