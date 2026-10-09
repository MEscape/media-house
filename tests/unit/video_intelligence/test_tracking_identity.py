"""Tracks from detections and anonymous identity clusters, on hand-made signals."""

from collections.abc import Callable

import pytest

from media_house.modules.video_intelligence.domain.identity import (
    TrackAppearance,
    cluster_tracks,
    cosine,
    mean_vector,
)
from media_house.modules.video_intelligence.domain.profiles import TrackingSettings
from media_house.modules.video_intelligence.domain.signals import DetectionRow
from media_house.modules.video_intelligence.domain.tracking import build_tracks
from media_house.modules.video_intelligence.domain.values import AnalyzerId, EntityKind
from tests.support.vi_signals import (
    basis,
    derived,
    detections,
    person,
    shot_signals,
)

SETTINGS = TrackingSettings()
FPS = 30.0


def moving(frame: int, speed: float = 0.01, **kwargs: object):  # type: ignore[no-untyped-def]
    """A person walking right: the box shifts ``speed`` of the picture per analysed frame."""
    shift = speed * (frame // 10)
    return person(frame, (0.2 + shift, 0.1, 0.5 + shift, 0.9), **kwargs)  # type: ignore[arg-type]


def test_a_person_seen_in_successive_frames_is_one_track() -> None:
    frames = list(range(0, 60, 10))
    signals = detections(frames, [moving(f) for f in frames])

    tracks = build_tracks(signals, [0], FPS, SETTINGS)

    assert len(tracks) == 1
    assert [p[0] for p in tracks[0].points] == frames
    assert tracks[0].kind is EntityKind.PERSON


def test_two_people_apart_are_two_tracks_that_keep_their_own_boxes() -> None:
    frames = list(range(0, 60, 10))
    left = [person(f, (0.05, 0.1, 0.3, 0.9)) for f in frames]
    right = [person(f, (0.65, 0.1, 0.95, 0.9)) for f in frames]

    tracks = build_tracks(detections(frames, left + right), [0], FPS, SETTINGS)

    assert len(tracks) == 2
    assert sorted(t.box.x0 for t in tracks) == [0.05, 0.65]
    assert all(len(t.points) == len(frames) for t in tracks)


def test_a_short_gap_is_an_occlusion_not_a_new_track() -> None:
    frames = list(range(0, 80, 10))
    seen = [moving(f) for f in frames if f not in (30, 40)]  # hidden for two analysed frames

    tracks = build_tracks(detections(frames, seen), [0], FPS, SETTINGS)

    assert len(tracks) == 1 and len(tracks[0].points) == len(frames) - 2


def test_a_long_absence_starts_a_new_track() -> None:
    frames = list(range(0, 400, 10))
    seen = [moving(f) for f in frames if f < 50 or f >= 300]  # gone for more than 1.5 s

    tracks = build_tracks(detections(frames, seen), [0], FPS, SETTINGS)

    assert len(tracks) == 2


def test_a_track_never_crosses_a_cut() -> None:
    frames = list(range(0, 120, 10))
    same_place = [person(f) for f in frames]

    tracks = build_tracks(detections(frames, same_place), [0, 60], FPS, SETTINGS)

    assert len(tracks) == 2
    assert [t.shot_index for t in tracks] == [0, 1]
    assert tracks[0].last < 60 <= tracks[1].first


def test_weak_detections_continue_a_track_but_never_start_one() -> None:
    frames = list(range(0, 50, 10))
    weak_only = [person(f, confidence=0.3) for f in frames]
    start_strong = [person(0, confidence=0.9)] + [person(f, confidence=0.3) for f in frames[1:]]

    assert build_tracks(detections(frames, weak_only), [0], FPS, SETTINGS) == []
    assert len(build_tracks(detections(frames, start_strong), [0], FPS, SETTINGS)[0].points) == 5


def test_a_single_sighting_is_noise() -> None:
    assert build_tracks(detections([0, 10], [person(0)]), [0], FPS, SETTINGS) == []


def test_different_kinds_and_labels_never_share_a_track() -> None:
    frames = [0, 10, 20, 30]
    dog = [person(f, label="dog", kind=EntityKind.ANIMAL) for f in frames]
    cat = [person(f, label="cat", kind=EntityKind.ANIMAL) for f in frames]

    tracks = build_tracks(detections(frames, dog + cat), [0], FPS, SETTINGS)

    assert sorted(t.label for t in tracks) == ["cat", "dog"]


def test_a_fast_mover_between_sparse_frames_is_followed_by_its_centre() -> None:
    frames = [0, 10, 20, 30]
    # the boxes barely overlap (small box, big jump) but the centres stay close
    rows = [person(f, (0.10 + 0.09 * i, 0.4, 0.18 + 0.09 * i, 0.6)) for i, f in enumerate(frames)]

    tracks = build_tracks(detections(frames, rows), [0], FPS, SETTINGS)

    assert len(tracks) == 1


def test_tracks_are_deterministic() -> None:
    frames = list(range(0, 60, 10))
    rows = [moving(f) for f in frames] + [person(f, (0.7, 0.2, 0.95, 0.8)) for f in frames]

    assert build_tracks(detections(frames, rows), [0], FPS, SETTINGS) == build_tracks(
        detections(frames, list(reversed(rows))), [0], FPS, SETTINGS
    )


def test_settings_are_validated() -> None:
    from media_house.modules.video_intelligence.domain.errors import InvalidProfile

    with pytest.raises(InvalidProfile):
        TrackingSettings(match_iou=0.0)
    with pytest.raises(InvalidProfile):
        TrackingSettings(min_detections=0)


# --- through the full derivation ---------------------------------------------------------------
def test_tracks_get_stable_ids_and_belong_to_their_shot() -> None:
    timeline = shot_signals(120, cuts=(60,))
    frames = list(range(0, 120, 10))
    rows = [person(f) for f in frames]

    result = derived(timeline, signals={AnalyzerId.ENTITIES: detections(frames, rows)})

    assert [t.track_id for t in result.tracks] == ["trk_0000000_1", "trk_0000060_1"]
    assert [t.shot_id for t in result.tracks] == ["shot_0000000", "shot_0000060"]
    shot = result.shots[0]
    assert shot.entities.track_ids == ("trk_0000000_1",)
    assert shot.entities.main_subject_track_id == "trk_0000000_1"
    assert shot.entities.counts == {"person": 1}


def test_a_shot_where_the_detector_found_nobody_says_so() -> None:
    timeline = shot_signals(120)
    result = derived(timeline, signals={AnalyzerId.ENTITIES: detections([0, 30, 60], [])})

    entities = result.shots[0].entities
    assert entities.ok and entities.reasons == ("no_entity_found",)
    assert entities.track_ids == () and entities.main_subject_track_id is None


# --- identity ----------------------------------------------------------------------------------
def appearance(
    track_id: str, shot: int, first: int, last: int, vector: tuple[float, ...]
) -> TrackAppearance:
    return TrackAppearance(track_id, shot, first, last, vector)


def test_tracks_that_look_alike_share_a_cluster_named_in_order_of_appearance() -> None:
    a1 = appearance("a1", 0, 0, 50, basis(0))
    b1 = appearance("b1", 0, 0, 50, basis(1))
    a2 = appearance("a2", 1, 100, 150, basis(0))

    clusters = cluster_tracks([b1, a2, a1], 0.9)

    assert [(c.cluster_id, c.track_ids) for c in clusters] == [
        ("person_A", ("a1", "a2")),
        ("person_B", ("b1",)),
    ]
    assert clusters[0].similarity == pytest.approx(1.0)


def test_two_people_on_screen_together_are_never_the_same_person() -> None:
    same_look_a = appearance("a", 0, 0, 50, basis(0))
    same_look_b = appearance("b", 0, 20, 70, basis(0))  # overlaps in time, same shot

    clusters = cluster_tracks([same_look_a, same_look_b], 0.5)

    assert len(clusters) == 2


def test_dissimilar_tracks_stay_apart_and_threshold_decides() -> None:
    near = tuple(0.6 if i == 0 else 0.8 if i == 1 else 0.0 for i in range(32))
    a, b = appearance("a", 0, 0, 10, basis(0)), appearance("b", 1, 20, 30, mean_vector([near]))

    assert len(cluster_tracks([a, b], 0.7)) == 2
    assert len(cluster_tracks([a, b], 0.5)) == 1
    assert cosine(a.vector, b.vector) == pytest.approx(0.6)


def test_cluster_letters_continue_after_z() -> None:
    from media_house.modules.video_intelligence.domain.identity import _name

    assert [_name(i) for i in (0, 25, 26, 27)] == ["person_A", "person_Z", "person_AA", "person_AB"]


def test_identity_clusters_appear_in_the_result_only_with_appearance_evidence() -> None:
    from media_house.modules.video_intelligence.domain.signals import (
        AppearanceRow,
        AppearanceSignals,
    )

    timeline = shot_signals(120, cuts=(60,))
    frames = list(range(0, 120, 10))
    rows = [person(f) for f in frames]
    appearances = AppearanceSignals(
        model="fake",
        frames=tuple(frames),
        rows=tuple(AppearanceRow(r.frame, r.box, basis(3)) for r in rows),
    )

    plain = derived(timeline, signals={AnalyzerId.ENTITIES: detections(frames, rows)})
    clustered = derived(
        timeline,
        signals={
            AnalyzerId.ENTITIES: detections(frames, rows),
            AnalyzerId.APPEARANCE: appearances,
        },
    )

    assert plain.identities == () and all(t.identity_cluster is None for t in plain.tracks)
    assert [i.cluster_id for i in clustered.identities] == ["person_A"]
    assert {t.identity_cluster for t in clustered.tracks} == {"person_A"}
    assert set(clustered.identities[0].track_ids) == {t.track_id for t in clustered.tracks}
    assert clustered.identities[0].method == "appearance_embedding_similarity"


# --- accuracy against known ground truth (IDF1) ---------------------------------------------
def idf1(truth: dict[str, dict[int, int]], found: dict[str, dict[int, int]]) -> float:
    """Identification F1: tracks and ground-truth people are matched one-to-one to maximise the
    number of frames both name the same detection."""
    scores = sorted(
        (
            (-len(set(t_frames) & set(f_frames)), t_id, f_id)
            for t_id, t_frames in truth.items()
            for f_id, f_frames in found.items()
        )
    )
    used_t: set[str] = set()
    used_f: set[str] = set()
    id_tp = 0
    for negative, t_id, f_id in scores:
        if negative == 0:
            break
        if t_id in used_t or f_id in used_f:
            continue
        used_t.add(t_id)
        used_f.add(f_id)
        id_tp += -negative
    truth_total = sum(len(v) for v in truth.values())
    found_total = sum(len(v) for v in found.values())
    return 2 * id_tp / (truth_total + found_total) if truth_total + found_total else 1.0


def test_tracking_identity_f1_on_a_scene_with_known_people() -> None:
    # three people walking at different speeds; the middle one is hidden for two analysed frames
    frames = list(range(0, 200, 10))
    people: dict[str, Callable[[int], tuple[float, float, float, float]]] = {
        "left": lambda f: (0.02 + 0.002 * f, 0.1, 0.17 + 0.002 * f, 0.9),
        "middle": lambda f: (0.40, 0.2, 0.55, 0.9),
        "right": lambda f: (0.95 - 0.15 - 0.001 * f, 0.15, 0.95 - 0.001 * f, 0.85),
    }
    hidden = {"middle": {80, 90}}
    rows: list[DetectionRow] = []
    truth: dict[str, dict[int, int]] = {name: {} for name in people}
    for f in frames:
        for name, box in people.items():
            if f in hidden.get(name, set()):
                continue
            row = person(f, box(f))
            rows.append(row)
            truth[name][f] = id(row)
    tracks = build_tracks(detections(frames, rows), [0], FPS, SETTINGS)
    by_row = {(r.frame, r.box): id(r) for r in rows}
    found = {
        f"track{n}": {p[0]: by_row[(p[0], p[1])] for p in track.points}
        for n, track in enumerate(tracks)
    }

    assert len(tracks) == 3
    assert idf1(truth, found) >= 0.95
