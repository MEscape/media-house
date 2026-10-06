"""The rules themselves are tested: each forbidden import in a synthetic tree must be reported."""

from pathlib import Path

import pytest

from tests.architecture.analysis import check_all, find_cycles, scan


def build(tmp_path: Path, files: dict[str, str]) -> dict:  # type: ignore[type-arg]
    for relative, source in files.items():
        path = tmp_path / "media_house" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return scan(tmp_path)


BASE = {
    "__init__.py": "",
    "shared/__init__.py": "",
    "shared/errors/__init__.py": "",
    "shared/logging/__init__.py": "",
    "core/__init__.py": "",
    "core/domain/__init__.py": "",
    "core/application/__init__.py": "",
    "core/infrastructure/__init__.py": "",
    "core/modules/__init__.py": "",
    "modules/__init__.py": "",
    "modules/a/__init__.py": "",
    "modules/a/domain/__init__.py": "",
    "modules/a/domain/entity.py": "",
    "modules/a/application/__init__.py": "",
    "modules/a/application/contracts.py": "",
    "modules/a/application/usecase.py": "",
    "modules/a/infrastructure/__init__.py": "",
    "modules/a/infrastructure/db.py": "",
    "modules/a/presentation/__init__.py": "",
    "modules/b/__init__.py": "",
    "modules/b/application/__init__.py": "",
    "modules/b/application/usecase.py": "",
    "modules/b/application/contracts.py": "",
    "modules/b/domain/__init__.py": "",
    "presentation/__init__.py": "",
    "bootstrap/__init__.py": "",
}


def violations_for(tmp_path: Path, file: str, source: str) -> list[str]:
    return check_all(build(tmp_path, {**BASE, file: source}))


def test_clean_tree_has_no_violations(tmp_path: Path) -> None:
    assert check_all(build(tmp_path, BASE)) == []


@pytest.mark.parametrize(
    ("file", "source", "expected"),
    [
        ("modules/a/domain/entity.py", "from PySide6.QtWidgets import QWidget", "Qt"),
        ("modules/a/domain/entity.py", "import sqlite3", "not allowed in the domain"),
        ("modules/a/domain/entity.py", "import logging", "not allowed in the domain"),
        ("modules/a/domain/entity.py", "import requests", "third-party"),
        (
            "modules/a/domain/entity.py",
            "from media_house.modules.a.infrastructure import db",
            "domain must not depend on infrastructure",
        ),
        (
            "modules/a/domain/entity.py",
            "from media_house.shared.logging import x",
            "domain may only use shared",
        ),
        (
            "modules/a/application/usecase.py",
            "from media_house.modules.a.infrastructure.db import X",
            "application must not depend on infrastructure",
        ),
        (
            "modules/a/application/usecase.py",
            "from media_house.modules.a.presentation import X",
            "application must not depend on presentation",
        ),
        ("modules/a/application/usecase.py", "from PySide6.QtCore import QObject", "Qt"),
        (
            "modules/a/application/usecase.py",
            "from media_house.core.modules import Container",
            "composition-only",
        ),
        (
            "modules/a/presentation/view.py",
            "from media_house.modules.a.domain.entity import E",
            "presentation must not depend on domain",
        ),
        (
            "modules/a/presentation/view.py",
            "from media_house.modules.a.infrastructure.db import D",
            "presentation must not depend on infrastructure",
        ),
        (
            "modules/a/infrastructure/db.py",
            "from media_house.modules.a.presentation import v",
            "infrastructure must not depend on presentation",
        ),
        ("modules/a/infrastructure/db.py", "from PySide6.QtCore import QObject", "Qt"),
        (
            "modules/a/application/usecase.py",
            "from media_house.modules.b.application.usecase import X",
            "must go through application.contracts",
        ),
        (
            "modules/a/application/usecase.py",
            "from media_house.modules.b.domain import X",
            "must go through application.contracts",
        ),
        (
            "presentation/shell.py",
            "from media_house.modules.a.application.contracts import X",
            "shell must not know feature modules",
        ),
        (
            "presentation/shell.py",
            "from media_house.core.modules.container import Container",
            "composition-only",
        ),
        (
            "core/application/port.py",
            "from media_house.modules.a.application.contracts import X",
            "core must not depend on feature modules",
        ),
        (
            "shared/errors/x.py",
            "from media_house.core.domain import X",
            "shared kernel must not depend",
        ),
        ("shared/errors/x.py", "from PySide6.QtCore import QObject", "Qt"),
        ("shared/errors/x.py", "import requests", "third-party"),
        (
            "shared/errors/x.py",
            "from media_house.bootstrap import x",
            "only bootstrap may import bootstrap",
        ),
        (
            "core/domain/x.py",
            "from media_house.bootstrap.application import A",
            "only bootstrap may import bootstrap",
        ),
        ("modules/a/application/usecase.py", "import subprocess", "subprocess may only"),
        ("modules/a/application/usecase.py", "from . import sibling", "relative import"),
        ("utils/__init__.py", "", "not in a recognised package"),
        ("shared/helpers/__init__.py", "", "new shared-kernel package"),
    ],
)
def test_forbidden_imports_are_reported(
    tmp_path: Path,
    file: str,
    source: str,
    expected: str,
) -> None:
    found = violations_for(tmp_path, file, source)
    assert any(expected in v for v in found), found


@pytest.mark.parametrize(
    ("file", "source"),
    [
        (
            "modules/a/application/usecase.py",
            "from media_house.modules.b.application.contracts import X",
        ),
        ("modules/a/application/usecase.py", "from media_house.modules.a.domain.entity import X"),
        ("modules/a/application/usecase.py", "from media_house.shared.logging import get_logger"),
        ("modules/a/domain/entity.py", "from media_house.shared.errors import X"),
        (
            "modules/a/presentation/view.py",
            "from media_house.modules.a.application.usecase import X",
        ),
        ("modules/a/presentation/view.py", "from media_house.presentation.extension import X"),
        ("modules/a/presentation/view.py", "from PySide6.QtWidgets import QWidget"),
        ("modules/a/infrastructure/db.py", "import sqlite3"),
        ("modules/a/infrastructure/db.py", "from media_house.modules.a.domain.entity import E"),
        ("modules/a/module.py", "from media_house.core.modules import Container"),
        ("modules/a/module.py", "from media_house.modules.a.infrastructure.db import D"),
        ("bootstrap/x.py", "from media_house.modules.a.module import M"),
        ("shared/errors/x.py", "import pydantic"),
        ("shared/logging/x.py", "import logging"),
        (
            "modules/a/domain/entity.py",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    pass",
        ),
    ],
)
def test_legitimate_imports_are_allowed(tmp_path: Path, file: str, source: str) -> None:
    found = violations_for(tmp_path, file, source)
    assert found == [], found


def test_subprocess_is_allowed_only_in_the_runner_adapter(tmp_path: Path) -> None:
    files = {
        **BASE,
        "core/infrastructure/process/__init__.py": "",
        "core/infrastructure/process/subprocess_runner.py": "import subprocess",
    }
    assert check_all(build(tmp_path, files)) == []


def test_type_checking_imports_are_still_layer_checked(tmp_path: Path) -> None:
    source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from media_house.modules.a.infrastructure.db import D"
    assert violations_for(tmp_path, "modules/a/domain/entity.py", source)


class TestCycles:
    def test_mutual_imports_are_detected(self, tmp_path: Path) -> None:
        files = {
            **BASE,
            "shared/errors/one.py": "from media_house.shared.errors import two",
            "shared/errors/two.py": "from media_house.shared.errors import one",
        }
        cycles = find_cycles(build(tmp_path, files))
        assert cycles == [["media_house.shared.errors.one", "media_house.shared.errors.two"]]

    def test_longer_cycles_are_detected(self, tmp_path: Path) -> None:
        files = {
            **BASE,
            "shared/errors/a.py": "import media_house.shared.errors.b",
            "shared/errors/b.py": "import media_house.shared.errors.c",
            "shared/errors/c.py": "import media_house.shared.errors.a",
        }
        assert len(find_cycles(build(tmp_path, files))) == 1

    def test_type_checking_and_lazy_imports_do_not_count_as_runtime_cycles(
        self, tmp_path: Path
    ) -> None:
        files = {
            **BASE,
            "shared/errors/a.py": "from media_house.shared.errors import b",
            "shared/errors/b.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from media_house.shared.errors import a",
            "shared/errors/c.py": "from media_house.shared.errors import d",
            "shared/errors/d.py": "def f():\n    from media_house.shared.errors import c",
        }
        assert find_cycles(build(tmp_path, files)) == []
