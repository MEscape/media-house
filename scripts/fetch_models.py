"""Download the AI models Media-House uses: the ONE explicit download step.

The application never downloads a model while it runs (a missing model is reported together with
the command below). Run this once, or whenever you want another model:

    uv run python scripts/fetch_models.py                          # Content Intelligence models
    uv run python scripts/fetch_models.py --llm Qwen/Qwen2.5-3B-Instruct
    uv run python scripts/fetch_models.py --speech large-v3        # WhisperX speech recognition
    uv run python scripts/fetch_models.py --speech-language en --speech-language de
    uv run python scripts/fetch_models.py --background-removal birefnet-general-lite
    uv run python scripts/fetch_models.py --diarization             # speaker labels (gated)
    uv run python scripts/fetch_models.py --print-dir

Content Intelligence models are stored as plain folders (no symlinks, so it works on Windows without
special privileges) in ``<cache dir>/content_intelligence/models/<org>--<name>``. Speech models go
to the Hugging Face / torch caches and background-removal models to rembg's own folder, exactly
where the engines look for them. Behind a TLS-inspecting proxy install ``truststore`` (for example
``uv run --system-certs --with truststore ...``); it is used automatically when present.
"""

import argparse
import sys
from pathlib import Path

from media_house.shared.filesystem import AppPaths

#: Apache-2.0, 118 M parameters, 50+ languages (German and English included), ~0.5 GB.
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
#: Apache-2.0, 1.5 B parameters, ~3 GB in fp16: fits an 8 GB GPU shared with a desktop.
#: Qwen/Qwen2.5-3B-Instruct (~6 GB) is better but needs roughly 10 GB of free GPU memory.
DEFAULT_LLM = "Qwen/Qwen2.5-1.5B-Instruct"

_WEIGHT_PATTERNS = ["*.json", "*.txt", "*.model", "*.safetensors", "merges.txt", "vocab.*"]
#: Files of other formats that would only waste disk space.
_IGNORED = ["*.bin", "*.h5", "*.msgpack", "*.onnx", "*.ot", "openvino/*", "onnx/*", "*.gguf"]


def _trust_system_certificates() -> None:
    try:
        import truststore  # type: ignore[import-not-found,unused-ignore]

        truststore.inject_into_ssl()
    except ImportError:
        pass


def model_dir(repo: str) -> Path:
    return (
        AppPaths.for_user().cache_dir / "content_intelligence" / "models" / repo.replace("/", "--")
    )


def fetch_content_model(repo: str) -> Path:
    from huggingface_hub import snapshot_download

    target = model_dir(repo)
    target.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo,
        local_dir=str(target),
        allow_patterns=_WEIGHT_PATTERNS,
        ignore_patterns=_IGNORED,
    )
    return target


def fetch_speech_model(model: str) -> str:
    import torch  # noqa: F401  # must load first: ctranslate2 reuses its CUDA DLLs
    import whisperx

    whisperx.load_model(model, "cpu", compute_type="int8", local_files_only=False)
    return f"speech model {model!r} (Hugging Face cache)"


def fetch_aligner(language: str) -> str:
    import torch  # noqa: F401
    from whisperx import alignment

    alignment.load_align_model(language_code=language, device="cpu", model_cache_only=False)
    return f"alignment model for {language!r}"


DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"


def fetch_diarization(repo: str) -> Path:
    """The pyannote models are gated: an account token and accepted terms are required.

    The token is read from ``HF_TOKEN`` or the cached ``huggingface-cli login``; it is never
    stored here. The folder is plain files under the audio models directory.
    """
    from huggingface_hub import snapshot_download

    models = AppPaths.for_user().cache_dir / "audio_intelligence" / "models"
    target = models / repo.replace("/", "--")
    target.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=repo, local_dir=str(target))
    return target


def fetch_background_removal(model: str) -> str:
    from rembg import new_session

    new_session(model, providers=["CPUExecutionProvider"])
    return f"background removal model {model!r} (rembg folder)"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--embedding", default=EMBEDDING_MODEL, help="sentence-embedding repo")
    parser.add_argument("--llm", default=DEFAULT_LLM, help="instruct language-model repo")
    parser.add_argument("--embedding-only", action="store_true")
    parser.add_argument("--llm-only", action="store_true")
    parser.add_argument("--speech", metavar="MODEL", help="WhisperX model, for example large-v3")
    parser.add_argument(
        "--speech-language",
        action="append",
        default=[],
        metavar="LANG",
        help="fetch the word-alignment model of a language (repeatable)",
    )
    parser.add_argument("--background-removal", metavar="MODEL", help="rembg model name")
    parser.add_argument(
        "--diarization",
        nargs="?",
        const=DIARIZATION_MODEL,
        metavar="REPO",
        help=f"speaker-diarization model (gated; default {DIARIZATION_MODEL})",
    )
    parser.add_argument("--print-dir", action="store_true", help="print the model folder and exit")
    args = parser.parse_args(argv)
    if args.print_dir:
        print(model_dir("x/y").parent)
        return 0
    _trust_system_certificates()

    others = bool(
        args.speech or args.speech_language or args.background_removal or args.diarization
    )
    done: list[str] = []
    if not others or args.embedding_only or args.llm_only:
        if not args.llm_only:
            print(f"fetching {args.embedding} ...", flush=True)
            done.append(str(fetch_content_model(args.embedding)))
        if not args.embedding_only:
            print(f"fetching {args.llm} ...", flush=True)
            done.append(str(fetch_content_model(args.llm)))
    if args.speech:
        print(f"fetching speech model {args.speech} ...", flush=True)
        done.append(fetch_speech_model(args.speech))
    for language in args.speech_language:
        print(f"fetching alignment model for {language} ...", flush=True)
        done.append(fetch_aligner(language))
    if args.background_removal:
        print(f"fetching background removal model {args.background_removal} ...", flush=True)
        done.append(fetch_background_removal(args.background_removal))
    if args.diarization:
        print(f"fetching diarization model {args.diarization} (needs a token) ...", flush=True)
        done.append(str(fetch_diarization(args.diarization)))
    for line in done:
        print(f"  -> {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
