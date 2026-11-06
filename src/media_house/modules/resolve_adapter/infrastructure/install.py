"""Where DaVinci Resolve's scripting library lives, and whether Resolve is running.

The locations are Blackmagic's documented defaults per operating system; a mapping with
``RESOLVE_SCRIPT_API`` / ``RESOLVE_SCRIPT_LIB`` (Blackmagic's own names) overrides them. Library
code never reads the process environment: the caller (a script, the CLI) builds the mapping.
"""

import importlib.machinery
import importlib.util
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.shared.errors import ExternalSystemError, ToolNotFoundError


@dataclass(frozen=True, slots=True)
class ResolveInstall:
    #: ``.../Developer/Scripting`` (documentation and the ``Modules`` folder).
    script_api_dir: Path | None
    #: The ``fusionscript`` library Resolve ships.
    script_lib: Path | None
    #: The name of the Resolve process.
    process_name: str
    platform: str

    @classmethod
    def defaults(cls, platform: str, env: Mapping[str, str] | None = None) -> "ResolveInstall":
        env = env or {}
        if platform.startswith("win"):
            data = Path(env.get("PROGRAMDATA", r"C:\ProgramData"))
            program = Path(env.get("PROGRAMFILES", r"C:\Program Files"))
            api = (
                data
                / "Blackmagic Design"
                / "DaVinci Resolve"
                / "Support"
                / "Developer"
                / "Scripting"
            )
            lib = program / "Blackmagic Design" / "DaVinci Resolve" / "fusionscript.dll"
            process = "Resolve.exe"
        elif platform == "darwin":
            api = Path(
                "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
            )
            lib = Path(
                "/Applications/DaVinci Resolve/DaVinci Resolve.app"
                "/Contents/Libraries/Fusion/fusionscript.so"
            )
            process = "Resolve"
        else:
            api = Path("/opt/resolve/Developer/Scripting")
            lib = Path("/opt/resolve/libs/Fusion/fusionscript.so")
            process = "resolve"
        if env.get("RESOLVE_SCRIPT_API"):
            api = Path(env["RESOLVE_SCRIPT_API"])
        if env.get("RESOLVE_SCRIPT_LIB"):
            lib = Path(env["RESOLVE_SCRIPT_LIB"])
        return cls(api, lib, process, platform)

    @property
    def installed(self) -> bool:
        return self.script_lib is not None and self.script_lib.is_file()


class ProcessProbe:
    """Is the Resolve process running? (Necessary for a connection, never sufficient.)"""

    def __init__(self, runner: ProcessRunner, install: ResolveInstall) -> None:
        self._runner = runner
        self._install = install

    def running(self) -> bool | None:
        """``None`` when the question could not be asked."""
        name = self._install.process_name
        if self._install.platform.startswith("win"):
            spec = ProcessSpec(
                "tasklist", ["/FI", f"IMAGENAME eq {name}", "/NH"], timeout_seconds=15
            )
            try:
                result = self._runner.run(spec)
            except (ToolNotFoundError, ExternalSystemError):
                return None
            return name.casefold() in result.stdout.casefold()
        spec = ProcessSpec("pgrep", ["-x", name], timeout_seconds=15)
        try:
            return self._runner.run(spec).exit_code == 0
        except (ToolNotFoundError, ExternalSystemError):
            return None


#: Loads Resolve's scripting module and returns the application object (or ``None``).
type ResolveLoader = Callable[[ResolveInstall], object | None]


def load_resolve(install: ResolveInstall) -> object | None:
    """Load ``fusionscript`` from Resolve's own library and ask it for the running application.

    This is the one place a vendor binary is loaded. It is loaded by path from the documented
    install location (never from the working directory), the same thing Blackmagic's own
    ``DaVinciResolveScript.py`` does. ``None`` means Resolve did not answer."""
    lib = install.script_lib
    if lib is None or not lib.is_file():
        raise ImportError("Resolve's scripting library was not found")
    loader = importlib.machinery.ExtensionFileLoader("fusionscript", str(lib))
    spec = importlib.util.spec_from_loader("fusionscript", loader)
    if spec is None:
        raise ImportError("Resolve's scripting library cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    app: object | None = module.scriptapp("Resolve")
    return app
