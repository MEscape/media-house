# ruff: noqa: T201, TID251, S603, S607 - benchmark CLI
"""Step 1: build the shot pool, the labelled queries and the shot keyframes (all into ``OUT``).

Reads only stored data through module contracts (Media Library, video_intelligence ShotIndexCatalog,
audio_intelligence AudioCatalog); nothing is analysed. Frames are extracted with ffmpeg.

    uv run python benchmarks/model_audit/broll/prepare.py
"""

import base64
import os
import re
import struct
import subprocess
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""

import numpy as np
from common import (
    BRIEF,
    EXTRA,
    FRAMES,
    FRAMES_PER_SHOT,
    HOME,
    OUT,
    QUERY_CHARS,
    read_json,
    write_json,
)

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    MediaLibrary,
    MediaQuery,
    MediaType,
)
from media_house.modules.production_project.application.contracts import sentence_lines
from media_house.modules.video_intelligence.application.contracts import ShotIndexCatalog
from media_house.shared.errors import Err


def said(text: str) -> list[str]:
    """Same word split as scratch/run_pipeline.py ``_said`` (the brief's phrase matcher)."""
    return re.findall(r"\w+", text.casefold())


def line_index(lines: list[dict], phrase: str) -> int | None:
    wanted = said(phrase)
    for i, line in enumerate(lines):
        words = said(line["text"])
        if any(words[j : j + len(wanted)] == wanted for j in range(len(words))):
            return i
    return None


def unpack(text: str) -> list[float]:
    raw = base64.b64decode(text)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


def main() -> None:
    app = Application.start(StartupOptions(home=HOME, shared_installs=True))
    try:
        library = app.container.resolve(MediaLibrary)
        shots_catalog = app.container.resolve(ShotIndexCatalog)
        audio = app.container.resolve(AudioCatalog)
        page = library.search(
            MediaQuery(
                media_types=frozenset({MediaType.VIDEO}),
                roles=frozenset({AssetRole.ORIGINAL}),
                page_size=200,
            )
        )
        pool: list[dict] = []
        vectors: list[list[float]] = []
        lines: list[dict] = []
        assets: list[dict] = []
        models: set[str] = set()
        for asset in sorted(page.items, key=lambda a: a.original_filename):
            path = library.local_path(asset.id)
            entry = {
                "id": asset.id,
                "file": asset.original_filename,
                "shots": 0,
                "transcript": False,
            }
            found = shots_catalog.shot_index(asset.id)
            index = None if isinstance(found, Err) else found.value
            if index is not None:
                dim = index.index.embedding_dimension
                if index.index.embedding_model:
                    models.add(index.index.embedding_model)
                for shot in index.index.shots:
                    start = shot.start.numerator / shot.start.denominator
                    end = shot.end.numerator / shot.end.denominator
                    vec = unpack(shot.embedding_f16) if shot.embedding_f16 else [0.0] * dim
                    pool.append(
                        {
                            "key": f"{asset.id}:{shot.shot_id}",
                            "asset": asset.id,
                            "file": asset.original_filename,
                            "path": str(path.value) if not isinstance(path, Err) else None,
                            "start": start,
                            "end": end,
                            "embedded": bool(shot.embedding_f16),
                        }
                    )
                    vectors.append(vec)
                entry["shots"] = len(index.index.shots)
                entry["embedding_model"] = index.index.embedding_model
            stored = audio.latest(asset.id)
            if not isinstance(stored, Err) and stored.value is not None:
                words = [(w.raw_word, w.start, w.end) for w in stored.value[0].transcript.words]
                entry["transcript"] = True
                for line in sentence_lines(asset.id, words):
                    lines.append(
                        {
                            "asset": asset.id,
                            "file": asset.original_filename,
                            "start": line.start,
                            "end": line.end,
                            "text": line.text,
                        }
                    )
            assets.append(entry)
    finally:
        app.shutdown()

    write_json(OUT / "assets.json", assets)
    write_json(OUT / "pool.json", {"embedding_models": sorted(models), "shots": pool})
    np.save(OUT / "stored_clip.npy", np.asarray(vectors, dtype=np.float32))
    write_json(OUT / "transcript_lines.json", lines)
    print(
        f"{len(assets)} video assets, {len(pool)} shots "
        f"({sum(s['embedded'] for s in pool)} embedded), "
        f"{len(lines)} transcript sentences, stored models {sorted(models)}"
    )

    # --- labels -> queries ------------------------------------------------------------------
    labels = [
        {**c, "provenance": "owner hand brief _qa/v8/run.json cover"}
        for c in read_json(BRIEF)["cover"]
    ]
    if EXTRA.exists():
        labels += read_json(EXTRA)
    queries: dict[str, dict] = {}
    for label in labels:
        i = line_index(lines, label["over"])
        if i is None:
            print(f"  ! nobody says the phrase of label on {Path(label['footage']).name}")
            continue
        j = line_index(lines[i:], label["until"]) if label.get("until") else 0
        span = [ln for ln in lines[i : i + (j or 0) + 1] if ln["asset"] == lines[i]["asset"]]
        text = " ".join(ln["text"].strip() for ln in span)[:QUERY_CHARS]
        file = Path(label["footage"]).name
        rng = (label["start"], label["start"] + label["seconds"]) if "start" in label else None
        relevant = [
            s["key"]
            for s in pool
            if s["file"] == file and (rng is None or (s["start"] < rng[1] and s["end"] > rng[0]))
        ]
        q = queries.setdefault(text, {"text": text, "labels": [], "relevant": []})
        q["labels"].append({"file": file, "range": rng, "provenance": label["provenance"]})
        q["relevant"] += [k for k in relevant if k not in q["relevant"]]
        if not relevant:
            print(f"  ! label on {file} has no shot in the pool")
    write_json(OUT / "queries.json", list(queries.values()))
    print(
        f"{len(labels)} labels -> {len(queries)} queries, "
        f"relevant shots per query {[len(q['relevant']) for q in queries.values()]}"
    )

    # --- keyframes (for models that need their own image tower) --------------------------------
    FRAMES.mkdir(parents=True, exist_ok=True)
    for n, shot in enumerate(pool):
        if not shot["path"]:
            continue
        for k in range(FRAMES_PER_SHOT):
            target = FRAMES / f"{n:04d}_{k}.png"
            if target.exists():
                continue
            t = shot["start"] + (k + 0.5) / FRAMES_PER_SHOT * (shot["end"] - shot["start"])
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    f"{t:.3f}",
                    "-i",
                    shot["path"],
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=640:-2",
                    str(target),
                ],
                check=True,
            )
    print(f"frames in {FRAMES}")


if __name__ == "__main__":
    main()
