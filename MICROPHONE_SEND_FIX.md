# Record and Send fix — revision 2

This patch addresses failure paths found while investigating unresponsive Record
and Send controls. The exact cause on your Windows machine has not been confirmed.

## Install

1. Stop the running app with Ctrl+C in its terminal.
2. Extract this ZIP. Copy the contents of its `voice-to-post-caring-bridge` folder
   over your existing project files, or run from the newly extracted folder.
   Keep your existing `.env` configuration; it is not included in this ZIP.
3. In your existing `(caringbridge)` environment, run `python app.py`.
4. Open http://127.0.0.1:8000 and press Ctrl+F5 once.
5. The top line should say **App connected · ready to type or record**.

Python requirements are unchanged. No application npm packages were added.

## What changed

- Frontend URLs have a version suffix and the app sends `Cache-Control: no-store`
  for its HTML/JS/CSS to prevent stale file mixtures after replacing the code.
- Startup failures, missing scripts, and unhandled errors display a visible alert.
  Session initialization has a timeout and a Retry connection button.
- Speech voice enumeration and speech cancellation failures no longer block typed
  input. A missing or broken canvas meter does not block recording.
- Send shows the user message before waiting for the model. A visible status
  distinguishes sending, waiting, connection failure, and server timeout.
- Record now uses explicit **Finish & send** by default, so submission does not
  depend on a silence threshold. Auto-send after a pause is optional.
- Added microphone selection, a level meter, cancelable permission requests, and
  specific guidance for blocked, missing, unavailable, or disconnected microphones.
- The audio context is resumed during the user gesture, while recording can still
  work if the level meter is unavailable.
- **Test microphone** records a local clip for playback. It does not upload that
  recording or send it to the model. A new test replaces the previous test clip.
- **Check audio / model** checks FFmpeg and, for Ollama, reachability and whether
  the configured model is installed. It does not test model generation or download
  anything. Other providers are identified but not probed.
- Audio conversion and recognition errors now surface useful setup guidance.
  Audio/model work runs off the async request loop. Confident one-word transcripts
  can pass recognition filtering (the previous two-word minimum rejected them).
- Removed the external font stylesheet import so the local UI uses installed fonts.

## Isolate the problem

1. **Type first:** enter “My sister is recovering at home” and press Send. It should
   immediately appear in the conversation, followed by a waiting status.
2. **Test the microphone:** select your actual microphone and click Test microphone.
   Grant permission if asked, talk, click Finish test, and play the sample. A silent
   sample means the issue is in the selected input, permissions, or device—not the
   language model. Check the device's hardware mute and Windows input settings.
3. **Send speech:** click Record, talk, then Finish & send. The page should show
   capture/upload status followed by the recognized words and the assistant reply.
4. **Check dependencies:** use Check audio / model. Missing FFmpeg can be installed
   in your existing environment with `conda install -c conda-forge ffmpeg`, followed
   by restarting the app. An unavailable Ollama service/model is reported separately.
5. If the buttons still fail, copy the visible startup error or diagnostics result.
   This is more informative than the normal “Uvicorn running” server startup line.

## Verification

- 41 Python tests passed, including audio upload-to-conversation routing,
  transcription cleanup, missing-FFmpeg reporting, cache policy, and one-word input.
- 8 dependency-free Node tests passed, covering recording, selected-device capture,
  a broken meter, permission denial, cancellation, and speech queuing.
- Full HTML and script loading was exercised in a DOM integration harness with a
  live FastAPI server and simulated microphone/STT/LLM/speech: typed Send and the
  Record → upload → assistant reply flow passed, including when the speech service
  throws errors. Local microphone test playback did not upload the test recording.
- No physical Windows microphone, real Whisper/Ollama model, or audible TTS quality
  was tested here. The DOM harness is not a substitute for a visual browser test.
