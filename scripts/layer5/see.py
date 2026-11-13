"""Seeing tools: turn a render into pictures a reviewer (or Claude, via Read) can judge by eye.

    python scripts/layer5/see.py sheet VIDEO OUT.jpg [--start S] [--end S] [--every S] [--cols 5]
    python scripts/layer5/see.py strip VIDEO OUT.jpg --start S [--seconds 3] [--every-frames 3] [--cols 6]
    python scripts/layer5/see.py at    VIDEO OUT.jpg T1 T2 T3 ... [--cols 4]
    python scripts/layer5/see.py diff  VIDEO_A VIDEO_B OUT.jpg T1 T2 ...      (A on top, B below)

`sheet` = contact sheet, `strip` = filmstrip (anticipation / overshoot / settle by eye),
`at` = frames at explicit times (cue times), `diff` = previous pass vs this pass.
Only ffmpeg is needed; every frame carries its time code.  Run through `uv.exe run --no-sync`
from WSL so ffmpeg.exe sees Windows paths.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

FONT = "C\\:/Windows/Fonts/arial.ttf"
TILE_W = 384


def _ffmpeg(args: list[str]) -> None:
    p = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, check=False)
    if p.returncode != 0:
        sys.exit(p.stderr.decode("utf-8", errors="ignore"))


def _label(offset: float = 0.0) -> str:
    return (
        f"drawtext=fontfile='{FONT}':text='%{{pts\\:hms\\:{offset}}}':"
        "x=6:y=h-th-6:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.6"
    )


def _duration(video: str) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
        capture_output=True, check=False,
    )
    return float(p.stdout.decode().strip())


def _tile(video: str, out: str, select: str, cols: int, count: int, *, start: float = 0.0, dur: float | None = None) -> None:
    span = ["-ss", f"{start}"] + (["-t", f"{dur}"] if dur is not None else [])
    rows = max(1, -(-count // cols))
    vf = f"{select},scale={TILE_W}:-2,{_label(start)},tile={cols}x{rows}"
    _ffmpeg([*span, "-i", video, "-vf", vf, "-frames:v", "1", "-q:v", "3", out])


def sheet(a: argparse.Namespace) -> None:
    end = a.end or _duration(a.video)
    count = int((end - a.start) / a.every) + 1
    _tile(a.video, a.out, f"fps=1/{a.every}", a.cols, count, start=a.start, dur=end - a.start)


def strip(a: argparse.Namespace) -> None:
    count = int(a.seconds * 30 / a.every_frames) + 1
    _tile(a.video, a.out, f"select='not(mod(n\\,{a.every_frames}))'", a.cols, count, start=a.start, dur=a.seconds)


def at(a: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for i, t in enumerate(a.times):
            f = str(Path(tmp) / f"{i:03d}.png")
            _ffmpeg(["-ss", f"{t}", "-i", a.video, "-frames:v", "1", f])
            files.append(f)
        args: list[str] = []
        for f in files:
            args += ["-i", f]
        n = len(files)
        rows = (n + a.cols - 1) // a.cols
        parts = "".join(f"[{i}:v]scale={TILE_W}:-2[v{i}];" for i in range(n))
        ins = "".join(f"[v{i}]" for i in range(n))
        _ffmpeg([*args, "-filter_complex", f"{parts}{ins}xstack=inputs={n}:layout="
                 + "|".join(f"{(i % a.cols) * TILE_W}_{(i // a.cols) * 220}" for i in range(n))
                 + f",pad=iw:{rows * 220}", "-frames:v", "1", a.out])


def diff(a: argparse.Namespace) -> None:
    for name, video in (("a", a.video_a), ("b", a.video_b)):
        sub = argparse.Namespace(video=video, out=a.out.replace(".", f"_{name}.", 1), times=a.times, cols=len(a.times))
        at(sub)
    _ffmpeg(["-i", a.out.replace(".", "_a.", 1), "-i", a.out.replace(".", "_b.", 1),
             "-filter_complex", "vstack", "-frames:v", "1", a.out])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheet")
    s.add_argument("video"); s.add_argument("out")
    s.add_argument("--start", type=float, default=0.0); s.add_argument("--end", type=float)
    s.add_argument("--every", type=float, default=10.0); s.add_argument("--cols", type=int, default=5)
    s.set_defaults(fn=sheet)
    t = sub.add_parser("strip")
    t.add_argument("video"); t.add_argument("out")
    t.add_argument("--start", type=float, required=True); t.add_argument("--seconds", type=float, default=3.0)
    t.add_argument("--every-frames", type=int, default=3); t.add_argument("--cols", type=int, default=6)
    t.set_defaults(fn=strip)
    c = sub.add_parser("at")
    c.add_argument("video"); c.add_argument("out"); c.add_argument("times", type=float, nargs="+")
    c.add_argument("--cols", type=int, default=4)
    c.set_defaults(fn=at)
    d = sub.add_parser("diff")
    d.add_argument("video_a"); d.add_argument("video_b"); d.add_argument("out")
    d.add_argument("times", type=float, nargs="+")
    d.set_defaults(fn=diff)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
