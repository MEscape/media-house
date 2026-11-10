# Hosted AI providers (free tiers) for CPU-only machines

On a machine without a usable GPU (e.g. Ryzen 5850U), local WhisperX large-v3 and local LLMs are the
bottleneck. Media-House can hand two jobs to free-tier cloud providers over OpenAI-compatible APIs.
Both are **opt-in and off by default**.

> **NDA warning.** A remote engine uploads client audio (transcription) or transcript text (LLM) to a
> third party. Use it only when the client agreed and the provider's terms fit. Every remote choice
> requires an explicit consent switch (`--allow-remote-ai` / `allow_remote_ai = true`); without it the
> run stops before anything is sent.

## Recommendation

Limits are free-tier figures researched in November 2026 and they change often. Check each provider's
limits page before relying on them.

| Task | Provider | Free-tier limits (approx.) | Privacy / NDA | Use it? |
| --- | --- | --- | --- | --- |
| Transcription | **Groq** `whisper-large-v3-turbo` | 2,000 req/day, 7,200 audio s/hour, 28,800 audio s/day; 25 MB per file; 10 s minimum billed per request | No training on API data | **Yes**: the default remote ASR |
| Story wording / LLM | **Groq** (e.g. `openai/gpt-oss-120b`) | about 1,000 req/day per model | No training on API data | **Yes**: first choice |
| LLM, high volume | **Cerebras** (e.g. `gpt-oss-120b`, Qwen models) | about 1M tokens/day | Check the current terms | Yes, as a fallback |
| LLM, model variety | **OpenRouter** `:free` models | 20 req/min; 50 req/day (1,000/day after a $10 credit purchase) | Depends on the upstream provider of each model | Only for tests, not client material |
| LLM | **Gemini** free tier | Generous | **Prompts may be used to improve Google products and read by human reviewers** | **Never for NDA material** |
| LLM, private | **Ollama** / local server | Your hardware | Stays on the machine | Yes, when the hardware allows |

Model ids change too often to have a default. List the current ones:
`GET https://api.groq.com/openai/v1/models` (with the key), or check the Cerebras and OpenRouter
model catalogues.

## Keys

Keys are read only from environment variables. They are held as `SecretStr` and never written to
logs (the log redactor masks keys and the adapters log no headers).

| Provider | Variable | Base URL used |
| --- | --- | --- |
| Groq | `MEDIA_HOUSE_SECRET_GROQ_API_KEY` | `https://api.groq.com/openai/v1` |
| Cerebras | `MEDIA_HOUSE_SECRET_CEREBRAS_API_KEY` | `https://api.cerebras.ai/v1` |
| OpenRouter | `MEDIA_HOUSE_SECRET_OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` |
| Gemini | `MEDIA_HOUSE_SECRET_GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| Ollama | none | `http://127.0.0.1:11434/v1` |

```powershell
$env:MEDIA_HOUSE_SECRET_GROQ_API_KEY = "gsk_..."
uv run python scratch/run_pipeline.py --input clip.mp4 --stages audio_analysis,story --story-llm `
    --transcription-engine groq --llm-provider groq --llm-model openai/gpt-oss-120b --allow-remote-ai
```

A missing key gives a clear message that names the variable. A missing LLM key makes the story
wording `unavailable`, and the deterministic plan still stands.

**Hugging Face token.** HF Inference free credits are tiny, so they are not useful for this pipeline.
The HF token is for **model downloads** only, for example the gated pyannote diarization models
(accept the terms on the model page, then run `scripts/fetch_models.py`).

## Behaviour

* **LLM** (`content_intelligence/infrastructure/loopback_llm.py`): one OpenAI-compatible adapter for
  local and hosted endpoints. Presets are in `LLM_PROVIDERS` (`domain/config.py`). A bearer key is
  sent only when `api_key_secret` is set. A non-loopback host still needs `allow_remote_endpoint`.
  429 and 5xx answers are retried up to `max_attempts` (default 3), honouring `Retry-After`. A wait
  longer than 60 s (usually a daily quota) fails at once with a rate-limit message. Every answer
  logs `usage_prompt`, `usage_completion`, the attempts and the seconds. A remote host is part of the
  engine identity, so cached replies never mix providers. Output validation by the callers is
  unchanged.
* **Transcription** (`audio_intelligence/infrastructure/groq_transcription.py`): replaces WhisperX
  when `AudioIntelligenceModule(remote_transcription=...)` is used. It reuses the prepared 16 kHz
  mono WAV, so the source is not probed or decoded again. It cuts the WAV into 10-minute windows
  with 10 s overlap and encodes each window to FLAC through the ProcessRunner/FFmpeg (well under the
  25 MB limit). It then requests `verbose_json` with segment and word timestamps at temperature 0.
  Each segment is kept from the window that owns its midpoint. The first detected language is
  pinned for the later windows.
* **Provenance and caching**: the transcript records `transcription_engine=groq`, the model, the
  endpoint as `model_source` and `device=remote`. The engine version contains the host, model and
  window sizes, so the cache key never matches a local WhisperX result.

## Known limits

* Groq word times come from Whisper itself, not from a wav2vec2 forced aligner. The transcript says
  `alignment_method = whisper_segment`, and captions warn that word times are estimated. The words
  carry no confidence.
* The language name to ISO code mapping covers about 25 common languages. An unlisted language is
  stored as `und` unless `--language` pins it.
* The pipeline still builds `TranscriptionConfig(engine="whisperx", model="large-v3")`. The remote
  engine ignores those two fields. Its distinct engine version keeps the cache keys apart, but the
  fingerprint's `engine`/`model` fields only become accurate once `media_pipeline` passes the engine
  choice through `StageSettings`.
* Diarization (pyannote) and acoustic analysis still run locally.
