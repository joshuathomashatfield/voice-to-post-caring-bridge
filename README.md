# CaringBridge First-Post Assistant

A voice-first, web-based writing assistant that helps someone compose their
first CaringBridge health-update post through a short conversation, using
speech-to-text, a local or self-hosted LLM, and text-to-speech -- while
keeping the person in full control of the final wording.

This is a research-prototype-quality build: it is meant to be run and used,
not just read.

---

## What it does

1. A short, conversational Q&A (speak or type) gathers only the information
   the user chooses to share -- what's happening, how things are going, what
   support would help, and what tone they want.
2. Once there's enough to work with, an LLM drafts a first post using
   **only** the facts the user provided.
3. The draft appears in an editable text box. The user can type changes
   directly, or ask for a revision ("make it shorter", "remove the hospital
   name") by voice or text.
4. The user can listen to the post read aloud, undo/redo revisions, restart,
   and export as `.txt` or `.md`.
5. **Nothing is ever published automatically.** There is no CaringBridge API
   integration; this tool only produces text for the user to paste in
   themselves.

---

## Architecture

```
caringbridge_assistant/
├── app.py                    FastAPI app: routes, request/response models
├── backend/
│   ├── config.py              Centralized settings (env-var driven)
│   ├── models.py              Pydantic models: session, post context, intents
│   ├── state.py                In-memory session store + version history
│   ├── conversation.py         Question policy + structured info extraction
│   ├── intents.py             Rule-based + LLM-fallback intent classification
│   ├── post_generator.py      Source-grounded draft generation & revision
│   ├── privacy.py             Regex-based sensitive-info scanning
│   ├── stt/
│   │   ├── audio_utils.py      Upload validation + ffmpeg normalization
│   │   ├── vad.py              Voice-activity detection (webrtcvad or energy-based)
│   │   └── whisper_engine.py   faster-whisper transcription + confidence checks
│   ├── tts/
│   │   └── tts_engine.py      TTS abstraction (browser / pyttsx3 / Piper)
│   └── llm/
│       ├── provider.py         LLMProvider interface + safe NullProvider fallback
│       └── ollama_provider.py  Ollama + OpenAI-compatible implementations
├── frontend/
│   ├── index.html
│   ├── styles.css
│   ├── app.js                 Session/turn handling, post editor, TTS playback
│   └── audio.js               Explicit microphone recording state machine
├── tests/                     pytest suite
├── requirements.txt
├── .env.example
└── exports/                   (created at runtime; not committed)
```

### Key design choices

- **Structured state, not string concatenation.** Every session tracks a
  `PostContext` (the facts) separately from `conversation_history` (the raw
  transcript), so drafting always works from validated structured data.
- **Source-grounded generation.** Every prompt to the LLM explicitly forbids
  inventing facts, and both drafting and revision are instructed to use only
  what's in `PostContext` / the existing post text.
- **Rules first, LLM second, for intent classification.** Obvious commands
  ("skip", "undo", "start over") are matched with regexes so they're instant
  and 100% reliable; only ambiguous phrasing falls through to the LLM
  classifier. Destructive intents (`RESTART`, `UNDO`, `REDO`,
  `DELETE_INFORMATION`) require a higher confidence threshold before being
  acted on (see `backend/intents.py::is_actionable`).
- **Browser-side TTS by default.** Rather than a server round-trip for every
  bit of playback, the frontend uses the Web Speech API's `SpeechSynthesis`,
  which starts instantly and is trivially interruptible ("Stop" button).
  `backend/tts/tts_engine.py` still exposes a swappable server-side
  abstraction (`pyttsx3` or Piper) for contexts where that's preferable.
- **MediaRecorder + HTTP upload, not WebRTC.** A short voice turn doesn't
  need a peer-connection lifecycle; a single recorded blob per turn is far
  more reliable and easier to reason about.
- **Explicit audio state machine.** `frontend/audio.js` defines a small state
  machine (`IDLE -> REQUESTING_PERMISSION -> LISTENING -> STOPPING -> IDLE`,
  with `ERROR` handling) instead of scattered booleans, so the mic button
  can't get into an inconsistent state or double-record.
- **Privacy by default.** Temporary audio files are deleted immediately after
  transcription. Sessions live in memory only (never written to disk) and
  expire automatically (`SESSION_TTL_MINUTES`). A "Clear session" control
  wipes everything for the current session on demand. Generated/edited posts
  are scanned for likely sensitive details (phone numbers, addresses, IDs)
  and flagged to the user before they'd share them.
- **Graceful degradation everywhere.** If the LLM is unreachable, if
  transcription fails, or if TTS isn't available, the user's existing
  answers and drafts are never destroyed -- they get a plain-language error
  and can keep working via typing/editing.

---

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) on your `PATH` (used to normalize recorded
  audio before transcription)
- An LLM backend: [Ollama](https://ollama.com/) running locally (default), an
  OpenAI-compatible API, or none (the app runs in a degraded "please
  configure an LLM" mode without one, useful for UI development/testing)
- A modern browser with microphone access (Chrome, Edge, Firefox, Safari)

## Setup

```bash
python -m venv .venv
```

Activate it:

```bash
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure environment variables:

```bash
cp .env.example .env
# edit .env as needed -- in particular LLM_PROVIDER / LLM_MODEL / OLLAMA_HOST
```

If you're using Ollama, pull a model first, e.g.:

```bash
ollama pull qwen3:8b
```

Run the app:

```bash
python app.py
# or
uvicorn app:app --reload
```

Then open **http://127.0.0.1:8000** in your browser.

### Microphone permissions

The first time you click "Speak", your browser will ask for microphone
permission. If you decline or it's unavailable, the mic button becomes
disabled with an explanatory message, and you can continue by typing --
nothing else in the app depends on voice input working.

---

## Running tests

```bash
pytest
```

The test suite covers intent parsing (including the confidence-gating
behavior for destructive actions), conversation stage progression and
skip/back logic, session state and version history (undo/redo/branching),
privacy scanning, audio upload validation, and post generation/revision
against the safe `NullProvider` fallback (so tests run without a live LLM).

### Manual test checklist

- [ ] Microphone: grant permission, speak an answer, confirm it transcribes and appears in the transcript
- [ ] STT accuracy: try a few different phrasings, background noise, silence (should ask you to repeat)
- [ ] Typing: answer entirely by typing, confirm the same flow works
- [ ] Draft generation: after ~3-5 answers, ask to "write the post" and confirm a draft appears
- [ ] Revision: ask to "make it shorter" / "remove [detail]" and confirm the draft updates without inventing facts
- [ ] Manual edit: type directly into the post box, then ask for a revision, and confirm your edit is preserved as the base
- [ ] Undo / redo: confirm both work and that a new edit after an undo correctly discards the old "future" branch
- [ ] Listen / Stop: confirm TTS starts and can be interrupted
- [ ] Restart: confirm it clears state and starts the question sequence over
- [ ] Export: download `.txt` and `.md`, confirm content matches the editor
- [ ] Mobile layout: check at a narrow viewport width
- [ ] Keyboard navigation: tab through all controls, confirm visible focus and that Enter sends a typed message

---

## Known limitations

- **No official CaringBridge integration.** This app does not publish
  anything; it produces text for the user to copy into CaringBridge
  themselves, by design.
- **STT accuracy** depends on the Whisper model size you configure
  (`STT_MODEL`); `small.en` is a reasonable CPU-friendly default, but
  `medium.en` or `large-v3` will transcribe more accurately at the cost of
  latency and memory.
- **Intent classification** for very unusual phrasing may fall through to
  `UNKNOWN` and ask the user to rephrase, rather than guessing.
- **Structured extraction** relies on the configured LLM returning valid
  JSON; a deterministic fallback stores the raw answer directly in the
  relevant field if that ever fails, so no information is silently lost, but
  it may be less well-organized than LLM-extracted facts.
- **Privacy scanning** is a heuristic regex-based safety net (phone numbers,
  emails, street addresses, ID-like numbers), not a guarantee -- it will miss
  things and occasionally over-flag.
- **Session storage is in-memory only** (by design, for privacy) -- restarting
  the server clears all active sessions. `SESSION_TTL_MINUTES` controls how
  long an idle session is kept before automatic cleanup.
- **Server-side TTS engines** (`pyttsx3`, Piper) are implemented but not the
  default; they're best-effort abstractions for contexts where browser TTS
  isn't suitable, and haven't been tuned for voice quality.
- Research-mode interaction metrics (turn counts, revision counts, etc.) are
  tracked on `SessionState` but are not persisted or exported anywhere yet --
  see "Recommended next steps."

---

## Recommended next steps

1. Wire up `ENABLE_RESEARCH_MODE` to actually export the anonymous
   per-session metrics already being tracked (`voice_turns`, `typed_turns`,
   `ai_revisions`, `manual_edits`, `undo_actions`, `stt_retry_attempts`,
   `used_tts`) to a research-mode-only log file, with no raw transcript
   content included.
2. Add streaming partial transcripts (Whisper supports chunked/streaming
   inference) so users see text appear while they're still speaking, rather
   than only after the trailing-silence cutoff.
3. Add a lightweight "confirm before restart/undo-all" modal for extra
   safety on destructive voice commands, beyond the confidence gate already
   in place.
4. Consider adding a plain-language "why we ask" tooltip next to each
   question, for first-time CaringBridge users unfamiliar with what a first
   post typically contains.
5. Internationalization: the question bank, system prompts, and STT language
   are currently English-only (`STT_LANGUAGE=en`).
