"""Honest adapters for capabilities this machine does not have (no model is shipped).

They never produce a matte or a reconstruction: they only say why not, and the planner records
the effect as ``not_available`` with a safer alternative.
"""


class UnavailableSubjectMatter:
    def identity(self) -> str:
        return "subject-matter: none"

    def unavailable(self) -> str | None:
        return "no video matting model is installed (background blur/replacement need one)"


class UnavailableInpainter:
    def identity(self) -> str:
        return "inpainter: none"

    def unavailable(self) -> str | None:
        return "no video inpainting model is installed (object removal needs one)"
