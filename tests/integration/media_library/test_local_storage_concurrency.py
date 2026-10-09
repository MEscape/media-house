"""Many writers storing the same content at once must all succeed and leave one intact blob."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from media_house.modules.media_library.domain.values import Checksum, MediaType, StorageKey
from media_house.modules.media_library.infrastructure.local_storage import LocalMediaStorage

pytestmark = pytest.mark.integration

WRITERS = 8
ROUNDS = 40


def _source(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def test_simultaneous_stores_of_identical_content_all_succeed(tmp_path: Path) -> None:
    storage = LocalMediaStorage(tmp_path / "blobs")
    payload = b"identical content " * 4096
    checksum = Checksum(hashlib.sha256(payload).hexdigest())
    key = StorageKey.for_content(MediaType.OTHER, checksum, ".json")
    sources = [_source(tmp_path, f"src{i}.json", payload) for i in range(WRITERS)]

    def hammer(source: Path) -> None:
        for _ in range(ROUNDS):
            storage.store(source, key, expected_checksum=checksum)
            assert storage.exists(key)

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        for future in [pool.submit(hammer, s) for s in sources]:
            future.result()  # re-raises any failure of a writer

    assert storage.get_path(key).read_bytes() == payload
    leftovers = list((tmp_path / "blobs").rglob("*.part"))
    assert leftovers == []


def test_different_blobs_in_one_shard_do_not_disturb_each_other(tmp_path: Path) -> None:
    storage = LocalMediaStorage(tmp_path / "blobs")
    payloads = [f"blob number {i} ".encode() * 512 for i in range(WRITERS)]

    def store(index: int) -> StorageKey:
        payload = payloads[index]
        checksum = Checksum(hashlib.sha256(payload).hexdigest())
        # all keys share the shard directory "00/00" to maximise contention
        key = StorageKey(f"other/00/00/{checksum.value}.json")
        source = _source(tmp_path, f"s{index}.json", payload)
        for _ in range(ROUNDS):
            storage.store(source, key, expected_checksum=checksum)
            assert storage.get_path(key).read_bytes() == payload
        return key

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        keys = list(pool.map(store, range(WRITERS)))

    assert len({k.value for k in keys}) == WRITERS


def test_a_key_can_never_leave_the_storage_root(tmp_path: Path) -> None:
    # keys are validated value objects: an escaping key cannot even be constructed
    for bad in ("../outside.json", "a/../../b.json", "/abs.json", "a//b.json"):
        with pytest.raises(Exception, match=r"Invalid storage key|dot segments"):
            StorageKey(bad)
    assert LocalMediaStorage(tmp_path / "blobs") is not None
