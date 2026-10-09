"""Model files, optional libraries, the vocabulary and the engine registry (no models loaded)."""

import hashlib
import threading
from pathlib import Path

import numpy as np
import pytest

from media_house.modules.video_intelligence.domain.profiles import CONTENT_PROFILES
from media_house.modules.video_intelligence.domain.values import AnalyzerId, DeviceKind
from media_house.modules.video_intelligence.domain.vocabulary import (
    CONTENT_PROFILE_OF,
    CONTENT_TYPES,
    ENVIRONMENTS,
    INDICATORS,
    label_prompts,
)
from media_house.modules.video_intelligence.infrastructure.model_files import (
    ModelSpec,
    ModelStore,
    library_missing,
)
from media_house.modules.video_intelligence.infrastructure.torch_support import (
    OnceLoaded,
    is_out_of_memory,
    resolve_device,
)
from media_house.modules.video_intelligence.infrastructure.torchvision_detector import (
    TorchvisionDetector,
    kind_of,
)
from media_house.modules.video_intelligence.module import classical_analyzers, signal_analyzers
from media_house.shared.errors import ExternalSystemError

PAYLOAD = b"model weights " * 1000
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def spec(url: str, prefix: str | None = None) -> ModelSpec:
    return ModelSpec("weights.bin", url, "MIT", prefix or DIGEST[:20])


class TestModelStore:
    def test_a_missing_model_says_what_to_download_and_where_unless_downloads_are_allowed(
        self, tmp_path: Path
    ) -> None:
        store = ModelStore(tmp_path / "models")
        wanted = spec("https://example.org/weights.bin")

        reason = store.missing_reason(wanted, allow_downloads=False)

        assert reason is not None
        assert "weights.bin" in reason and "https://example.org/weights.bin" in reason
        assert "MIT" in reason and "allow model downloads" in reason
        assert store.missing_reason(wanted, allow_downloads=True) is None

    def test_a_present_file_is_used_without_any_network(self, tmp_path: Path) -> None:
        store = ModelStore(tmp_path)
        (tmp_path / "weights.bin").write_bytes(PAYLOAD)
        wanted = spec("https://invalid.example/never-fetched")

        assert store.missing_reason(wanted, False) is None
        assert store.ensure(wanted, allow_downloads=False) == tmp_path / "weights.bin"

    def test_a_file_that_does_not_match_its_pinned_checksum_is_refused(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "weights.bin").write_bytes(b"swapped")

        with pytest.raises(ExternalSystemError, match="checksum"):
            ModelStore(tmp_path).ensure(spec("https://x.example/w"), allow_downloads=False)

    def test_without_permission_a_missing_file_is_not_downloaded(self, tmp_path: Path) -> None:
        with pytest.raises(ExternalSystemError, match="allow model downloads"):
            ModelStore(tmp_path).ensure(spec("https://x.example/w"), allow_downloads=False)

    def test_an_allowed_download_is_verified_and_stored_once(self, tmp_path: Path) -> None:
        source = tmp_path / "origin" / "weights.bin"
        source.parent.mkdir()
        source.write_bytes(PAYLOAD)
        store = ModelStore(tmp_path / "models")

        path = store.ensure(spec(source.as_uri()), allow_downloads=True)

        assert path.read_bytes() == PAYLOAD
        assert not list((tmp_path / "models").glob("*.part"))

    def test_a_download_with_the_wrong_checksum_is_discarded(self, tmp_path: Path) -> None:
        source = tmp_path / "weights.bin"
        source.write_bytes(b"something else")
        store = ModelStore(tmp_path / "models")

        with pytest.raises(ExternalSystemError, match="checksum"):
            store.ensure(spec(source.as_uri()), allow_downloads=True)

        assert not store.present(spec(source.as_uri())) and not list(
            (tmp_path / "models").glob("*.part")
        )

    def test_other_searched_folders_are_used_before_downloading(self, tmp_path: Path) -> None:
        elsewhere = tmp_path / "hub"
        elsewhere.mkdir()
        (elsewhere / "weights.bin").write_bytes(PAYLOAD)
        store = ModelStore(tmp_path / "models", elsewhere)

        assert (
            store.ensure(spec("https://x.example/w"), allow_downloads=False)
            == elsewhere / "weights.bin"
        )


def test_a_missing_library_is_reported_by_name_and_present_ones_are_not() -> None:
    assert library_missing("numpy", "scipy") is None
    message = library_missing("numpy", "definitely_not_installed_xyz")
    assert message is not None and "definitely_not_installed_xyz" in message and "vision" in message


def test_a_model_is_loaded_once_even_when_many_threads_ask_at_once() -> None:
    loads: list[int] = []
    holder: OnceLoaded[int] = OnceLoaded()

    def load() -> int:
        loads.append(1)
        return 42

    results: list[int] = []
    threads = [
        threading.Thread(target=lambda: results.append(holder.get("m", load))) for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [42] * 8 and len(loads) == 1
    holder.drop("m")
    assert holder.get("m", load) == 42 and len(loads) == 2


def test_the_cpu_is_chosen_when_asked_for_and_memory_errors_are_recognised() -> None:
    assert resolve_device(DeviceKind.CPU) == "cpu"
    assert resolve_device(DeviceKind.AUTO) in {"cpu", "cuda"}
    assert is_out_of_memory(RuntimeError("CUDA out of memory. Tried to allocate 2 GiB"))
    assert not is_out_of_memory(RuntimeError("shape mismatch"))


def test_running_out_of_gpu_memory_moves_the_detector_to_the_cpu_and_carries_on(
    tmp_path: Path,
) -> None:
    pytest.importorskip("torch")

    class Network:
        def __init__(self) -> None:
            self.where = "cuda"
            self.attempts = 0

        def to(self, device: str) -> "Network":
            self.where = device
            return self

        def __call__(self, images: list[object]) -> list[dict[str, object]]:
            self.attempts += 1
            if self.where != "cpu":
                raise RuntimeError("CUDA out of memory")
            return [{"ok": True} for _ in images]

    detector = TorchvisionDetector(ModelStore(tmp_path))
    network = Network()
    picture = np.zeros((8, 8, 3), dtype=np.uint8)

    found = detector._detect(network, [picture], "cuda")

    assert found == [{"ok": True}] and network.where == "cpu" and network.attempts == 2
    assert detector.device(DeviceKind.GPU) == "cpu"  # and it stays there


def test_detector_classes_map_to_coarse_kinds() -> None:
    assert [kind_of(n).value for n in ("person", "dog", "car", "cup")] == [
        "person",
        "animal",
        "vehicle",
        "object",
    ]


class TestVocabulary:
    def test_prompts_are_unique_prefixed_and_stable(self) -> None:
        prompts = label_prompts()

        names = [n for n, _ in prompts]
        assert len(set(names)) == len(names)
        assert prompts == label_prompts()
        assert len(prompts) == len(CONTENT_TYPES) + len(ENVIRONMENTS) + len(INDICATORS)
        assert all(n.startswith(("content:", "environment:", "indicator:")) for n in names)

    def test_every_content_label_selects_an_existing_conditioning_profile(self) -> None:
        assert set(CONTENT_PROFILE_OF.values()) <= set(CONTENT_PROFILES)
        assert set(CONTENT_PROFILE_OF) <= set(CONTENT_TYPES)


class TestEngineRegistry:
    def test_every_analyzer_has_an_engine_and_the_classical_ones_need_nothing(self) -> None:
        engines = signal_analyzers()

        assert set(engines) == set(AnalyzerId)
        for engine in classical_analyzers().values():
            assert engine.unavailable(False) is None and engine.device(DeviceKind.GPU) == "cpu"

    def test_identity_and_availability_are_cheap_and_never_load_a_model(
        self, tmp_path: Path
    ) -> None:
        engines = signal_analyzers(tmp_path)

        for analyzer, engine in engines.items():
            assert engine.analyzer is analyzer
            identity = engine.identity()
            assert identity and all(isinstance(v, str) for v in identity.values())
            reason = engine.unavailable(False)
            assert reason is None or isinstance(reason, str)

    def test_model_engines_name_a_missing_model_instead_of_failing(self, tmp_path: Path) -> None:
        engines = signal_analyzers(tmp_path / "empty")

        for analyzer in (AnalyzerId.FACES, AnalyzerId.BODY):
            reason = engines[analyzer].unavailable(False)
            assert reason is not None and ("not installed" in reason or ".task" in reason)

    def test_the_vision_language_model_reports_its_own_missing_folder(self, tmp_path: Path) -> None:
        from media_house.modules.video_intelligence.infrastructure.vlm_descriptions import (
            TransformersVlm,
        )

        engine = TransformersVlm(directory=tmp_path / "missing")

        assert "missing" in (engine.unavailable(True) or "")
        assert TransformersVlm(directory=tmp_path).unavailable(False) is None
