# ruff: noqa: T201 - benchmark CLI
"""Step 3: rank the identical pool for the identical queries; write metrics.

    uv run python benchmarks/model_audit/broll/score.py

Pool = every shot that has a stored CLIP embedding (shots the pipeline can never find by picture are
left out for every system, so all systems rank the same rows). Recall@k = share of queries with at
least one relevant shot in the top k (hit rate); MRR = mean of 1 / rank of the first relevant shot.
Writes ``OUT/results.json`` and ``OUT/results.md`` (contains client text: stays outside the repo).
"""

import numpy as np
from common import OUT, read_json, write_json

#: system -> (query vectors from, shot vectors from); "stored" = the library's CLIP shot vectors.
SYSTEMS = {
    "A  clip-b32 text vs stored": ("clip_current", "stored"),
    "A' clip-b32 text vs re-embedded frames": ("clip_current", "clip_frames"),
    "B  mclip-v1 text vs stored": ("mclip", "stored"),
    "B' mclip-v1 text vs re-embedded frames": ("mclip", "clip_frames"),
    "C  siglip2-b16 both towers": ("siglip2", "siglip2"),
    # Both text towers live in the same B/32 image space: averaging the query vectors = averaging
    # the two cosines. No re-embedding; tests whether they fail on different queries.
    "AB mean(A,B) text vs stored": ("clip_current+mclip", "stored"),
}
KS = (1, 5, 10)


def load(name: str, part: str) -> np.ndarray | None:
    if name == "stored":
        return np.load(OUT / "stored_clip.npy")
    if "+" in name:
        parts = [load(n, part) for n in name.split("+")]
        return None if any(p is None for p in parts) else sum(parts)
    path = OUT / f"emb_{name}.npz"
    return np.load(path)[part] if path.exists() and part in np.load(path) else None


def ranks(q: np.ndarray, s: np.ndarray, relevant: list[list[int]]) -> list[int]:
    out = []
    for vector, rel in zip(q, relevant, strict=True):
        order = np.argsort(-(s @ vector), kind="stable")
        position = {int(row): i + 1 for i, row in enumerate(order)}
        out.append(min(position[r] for r in rel))
    return out


def metrics(rs: list[int]) -> dict[str, float]:
    m = {f"R@{k}": sum(r <= k for r in rs) / len(rs) for k in KS}
    m["MRR"] = float(np.mean([1 / r for r in rs]))
    return m


def main() -> None:
    pool = read_json(OUT / "pool.json")["shots"]
    queries = read_json(OUT / "queries.json")
    keep = [i for i, s in enumerate(pool) if s["embedded"]]
    row_of = {pool[i]["key"]: n for n, i in enumerate(keep)}
    broll = [n for n, i in enumerate(keep) if "_talking_" not in pool[i]["file"]]
    for q in queries:
        q["owner"] = any(lb["provenance"].startswith("owner") for lb in q["labels"])
        q["rel"] = [row_of[k] for k in q["relevant"] if k in row_of]
        files = {lb["file"] for lb in q["labels"]}
        q["rel_file"] = [n for n, i in enumerate(keep) if pool[i]["file"] in files]
    idx = [i for i, q in enumerate(queries) if q["rel"]]  # rows of the query vectors to keep
    queries = [queries[i] for i in idx]

    results: dict = {"pool_shots": len(keep), "broll_shots": len(broll), "systems": {}}
    for system, (qname, sname) in SYSTEMS.items():
        qv, sv = load(qname, "q"), load(sname, "s")
        if qv is None or sv is None:
            print(f"skip {system}: embeddings missing")
            continue
        sv = sv[keep]
        sv = sv / np.maximum(np.linalg.norm(sv, axis=1, keepdims=True), 1e-12)
        qv = qv[idx]
        entry: dict = {}
        for subset, chosen in (
            ("owner", [q["owner"] for q in queries]),
            ("all", [True] * len(queries)),
        ):
            pick = [i for i, c in enumerate(chosen) if c]
            rs = ranks(qv[pick], sv, [queries[i]["rel"] for i in pick])
            rf = ranks(qv[pick], sv, [queries[i]["rel_file"] for i in pick])
            # B-roll-only pool: drop the talking-head (A-roll) recordings from the ranking.
            sub = sv[broll]
            remap = {r: n for n, r in enumerate(broll)}
            rb = ranks(
                qv[pick], sub, [[remap[r] for r in queries[i]["rel"] if r in remap] for i in pick]
            )
            entry[subset] = {
                "shot": metrics(rs),
                "file": metrics(rf),
                "shot_broll_pool": metrics(rb),
                "ranks": rs,
                "file_ranks": rf,
                "broll_ranks": rb,
                "queries": pick,
            }
        results["systems"][system] = entry
    results["queries"] = [
        {
            "id": f"Q{i + 1}",
            "owner": q["owner"],
            "text": q["text"],
            "labels": q["labels"],
            "relevant_shots": len(q["rel"]),
            "file_shots": len(q["rel_file"]),
        }
        for i, q in enumerate(queries)
    ]
    results["timing"] = {
        n: read_json(OUT / f"emb_{n}.json")
        for n in ("clip_current", "clip_frames", "mclip", "siglip2")
        if (OUT / f"emb_{n}.json").exists()
    }
    write_json(OUT / "results.json", results)

    lines = [f"pool: {len(keep)} embedded shots ({len(broll)} without talking-head recordings)", ""]
    for subset in ("owner", "all"):
        lines += [
            f"## {subset} queries",
            "",
            "| system | R@1 | R@5 | R@10 | MRR | file R@1 | file MRR "
            "| B-roll-pool R@1 | B-roll-pool MRR |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for system, entry in results["systems"].items():
            e = entry[subset]
            m, f, b = e["shot"], e["file"], e["shot_broll_pool"]
            lines.append(
                f"| {system} | {m['R@1']:.2f} | {m['R@5']:.2f} | {m['R@10']:.2f} | {m['MRR']:.3f} "
                f"| {f['R@1']:.2f} | {f['MRR']:.3f} | {b['R@1']:.2f} | {b['MRR']:.3f} |"
            )
        lines.append("")
    lines += [
        "## per-query rank of first relevant shot (file-level rank in brackets)",
        "",
        "| query | owner | rel | random E[rank] | "
        + " | ".join(s.split()[0] for s in results["systems"])
        + " |",
        "| --- | --- | --- | --- | " + " | ".join("---" for _ in results["systems"]) + " |",
    ]
    for i, q in enumerate(results["queries"]):
        cells = [
            f"{e['all']['ranks'][i]} ({e['all']['file_ranks'][i]})"
            for e in results["systems"].values()
        ]
        lines.append(
            f"| {q['id']} {q['text'][:60]} | {q['owner']} | {q['relevant_shots']} "
            f"| {(len(keep) + 1) / (q['relevant_shots'] + 1):.1f} | " + " | ".join(cells) + " |"
        )
    (OUT / "results.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
