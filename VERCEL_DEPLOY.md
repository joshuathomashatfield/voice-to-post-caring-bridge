# Deploy this CaringBridge prototype to Vercel for $0

This repository is prepared for a simple hosted architecture:

**Browser -> Vercel FastAPI -> Groq (LLM + Whisper STT)**

Browser `SpeechSynthesis` remains the TTS layer. **No Redis, Upstash, or other
database is required.** The full prototype session is stored in the
participant's browser tab using `sessionStorage` and is sent to FastAPI with
each request.

## 1. Create a free Groq API key

Create a GroqCloud account and API key. The app uses Groq for:

- chat / draft generation
- microphone transcription with `whisper-large-v3-turbo`

The API key stays in Vercel environment variables and is never placed in the
browser JavaScript.

## 2. Push this folder to GitHub

From the repository root:

```bash
git add .
git commit -m "Prepare CaringBridge assistant for Vercel"
git push
```

## 3. Import the repository into Vercel

In Vercel choose **Add New -> Project**, import the GitHub repository, and use
the default build/framework settings. The root `app.py` exports the FastAPI
`app` object.

## 4. Add Vercel environment variables

Under **Project -> Settings -> Environment Variables**, add:

```text
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-20b

STT_PROVIDER=groq
GROQ_STT_MODEL=whisper-large-v3-turbo
GROQ_API_KEY=<your Groq API key>

SESSION_BACKEND=client

ENABLE_TTS=true
TTS_ENGINE=browser
ENABLE_AUTOSAVE=true
DEBUG=false
```

Then redeploy.

If `openai/gpt-oss-20b` is no longer available in your Groq account, replace
only `LLM_MODEL` with a current Groq chat-completions model.

## How browser-only state works

Vercel functions are ephemeral, so the app does not rely on Python process
memory. Instead:

1. `/api/session` creates a new `SessionState`.
2. The browser stores that state in `sessionStorage`.
3. Each API request sends the current state back to FastAPI.
4. FastAPI updates it and returns the new state.
5. The browser immediately stores the returned version.

This means there is no external session database and no Upstash account to
configure.

### Important limitation

`sessionStorage` is scoped to the current browser tab. Reloading the same tab
keeps the session, but closing the tab/window or opening the app in a new tab
starts a new session. That behavior is appropriate for a short unattended
usability test, but it is not intended as production persistence.

The browser state may include the text entered by the participant. For this
class prototype, use only the fictional/evaluation information authorized for
the study. Do not treat this prototype architecture as a production healthcare
data system.

## 5. Test the public URL before UserTesting

Open the deployed `https://...vercel.app` URL in an incognito/private browser
that is not logged into your development accounts. Check:

1. The page loads without a local Python server.
2. A session starts.
3. Typed messages receive AI responses.
4. Microphone permission works.
5. A short recording is transcribed.
6. The transcript appears in the conversation.
7. Create/update draft generates a post.
8. Manual editing and AI revision work.
9. Undo/redo works.
10. Reload the **same tab** and confirm the session survives.
11. Copy/download the draft.
12. Clear Session starts over.

Also test Chrome and one additional browser if practical.

## Local development

Install the hosted-path dependencies:

```bash
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`, add your Groq key, then run:

```bash
uvicorn app:app --reload
```

The same browser-state flow works locally. If you specifically want the old
backend-memory + Ollama/local-Whisper development path, install
`requirements-local.txt` and set `SESSION_BACKEND=memory`, `STT_PROVIDER=local`,
and `LLM_PROVIDER=ollama`.
