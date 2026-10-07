"""Bakes a colour plan into a ``.cube`` 3D LUT that FFmpeg applies to every frame."""

from pathlib import Path

from media_house.modules.video_improvement.domain.planning import ColorPlan
from media_house.modules.video_improvement.infrastructure import color_science as cs


class LutBaker:
    """Structurally implements ``application.ports.ColorBaker``."""

    @property
    def identity(self) -> str:
        return f"lut-baker-{cs.REVISION}"

    def look_identity(self, lut_path: str) -> str:
        return cs.read_cube(Path(lut_path)).sha256

    def bake(self, plan: ColorPlan, size: int, destination: Path) -> None:
        look = cs.read_cube(Path(plan.look_lut)) if plan.look_lut else None
        table = cs.bake(cs.ColorTransform(plan, look), size)
        cs.write_cube(destination, table, f"media-house {plan.input_color} to rec709")
