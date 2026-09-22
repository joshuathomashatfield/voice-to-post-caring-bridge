# Shared representation and conversational speech update

Based on repository commit `a30ffd7379ba64df8490f0f872a69e78e98b466e`.

## Review findings and implemented changes

1. **Hidden drafting context:** The original interface exposed the transcript and
   final draft, but not the structured facts driving generation. Added an editable
   “What I’ve understood” panel with names, situation, updates, support, tone,
   communication preferences, privacy constraints, and notes. The API returns a
   shared state snapshot after turns and edits. Corrections become instructions
   for subsequent drafting, including removal of previous field values.
2. **Silent ordinary replies:** Originally only READ_POST supplied speech. Normal
   replies, questions, and transcription retry messages now play automatically
   while Speak replies is enabled. Hear last reply provides a direct playback
   action; Read draft explicitly reads the post.
3. **Questionnaire-like responses:** Extraction now also requests a brief,
   grounded acknowledgement of the latest answer. It uses recent turns to avoid
   treating each answer in isolation. This reuses the existing model request.
   Prompts discourage canned praise, assumed emotions, and medical reassurance.
4. **Speech control:** Added selectable installed/browser voices, natural/gentle/
   faster rates, sentence-aware playback, brief pauses, and cancellation guards
   against late callbacks. Starting recording cancels speech. Recording remains
   explicitly user-controlled, with a slightly longer 1.8-second silence window.
5. **Stale draft races:** Manual saves are serialized and awaited before further
   actions. Failed saves preserve the editor and stop the dependent action. Empty
   drafts can be saved. Restart clears the displayed draft as well as the server
   state. Version/source labels and working undo/redo availability are visible.
6. **Unclear note/draft relationship:** Changing notes marks the draft for review;
   it does not silently regenerate. Returning to a draft from an older context
   also shows the notice. Shared notes remain editable independently of the draft.
7. **Session recovery:** Reconnects to a live session on tab reload and restores
   cached tab draft edits. After server loss, the draft can be recovered into a
   new session. Storage access failures do not prevent normal use.
8. **Recording robustness:** Selects a supported recording format, keeps its MIME
   type through upload, releases microphone tracks on setup failure, and ignores
   permission results after cancellation. Adds browser echo/noise suppression hints.
9. **Conversation correctness:** Go back repeats the previous question even when
   its field is filled. Repeated extracted lists are deduplicated. Failed fact
   extraction keeps uncertain words under notes rather than guessing a fact field.
   A longer answer containing “I don’t know” is no longer automatically skipped.

## Dependency and setup impact

`requirements.txt` is byte-for-byte unchanged. FastAPI, Pydantic, faster-whisper,
Ollama/OpenAI-compatible HTTP, MediaRecorder, and browser speech remain the stack.
No application npm packages, new cloud account, or extra TTS model is required.
Keep your existing Python environment and `.env`; run `python app.py` as before.
If extracting into a new directory, copy your existing `.env` into it privately.
Do not replace it with the example unless you intend to reset configuration.

The archive excludes `.env`, `.git`, Python caches, temporary recordings, exports,
and test-environment installations. `.env.example` is included.

## Validation

- 37 Python tests passed, including 8 new regression tests for spoken turn
  responses, visible context, correction propagation, manual draft preservation,
  restart, stop speech, go-back, fallback extraction, deduplication, and stale
  context after undo (some concerns share a test).
- 3 dependency-free Node speech tests passed: cancel-before-start, ignored late
  callbacks after interruption, and chosen voice/rate with bounded text chunks.
- DOM integration checks passed against a live local API with a stubbed speech
  engine: session startup, speech dispatch, saving shared notes, generation,
  immediate manual edit followed by revision, undo, and saving an empty draft.
  The temporary DOM test tooling is not an application dependency.
- JavaScript syntax checks and Python compilation passed.
- Full visual browser testing was unavailable: this environment had no browser
  installed, and the browser download could not complete. Layout should be
  checked at your usual resolution and at a narrow window size.
- No real Ollama model, physical microphone, or Windows TTS voice was exercised.
  Model output quality, transcription quality, audible prosody, and latency need
  a local end-to-end check. Automated tests use controlled model/speech substitutes.

## Quick local acceptance check

1. Start Ollama and the app, open localhost:8000, and click Hear last reply.
2. Select a voice, speak a short answer, and confirm the spoken acknowledgement
   and next question match the transcript. Press Speak while it is talking.
3. Correct a name in What I’ve understood, save it, and create a draft. Verify
   the name and the requested privacy choices in the actual generated wording.
4. Type directly into the draft and immediately ask for a revision. Confirm the
   new wording was used as the base. Undo and redo the revision.
5. Reload the tab, verify the draft and notes, then download a .txt copy.

## Remaining recommendations

- Add per-fact transcript provenance and an explicit “needs clarification” label;
  extraction validation checks shape, not factual accuracy. Direct notes and the
  draft remain the user's review surface in this release.
- For stronger naturalness, evaluate a neural TTS engine in a separate change.
  This release improves dialogue, turn handling, voice choice, and pacing within
  your existing dependency structure. Browser voice quality varies.
- Consider confirmation for destructive voice commands, and schedule the existing
  session-expiration sweep. Those remain follow-up work.
- Privacy omissions and context corrections are model instructions, not a verified
  redaction guarantee. Removing a note does not erase prior versions/transcripts;
  Clear session removes the current session. Review before sharing.
- Scope remains a local, single-user prototype; simultaneous edits from multiple
  clients are not protected by server-side revision locks.
