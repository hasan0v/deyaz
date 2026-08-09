# Architecture

Dikte is a native Windows desktop application. It keeps capture, transcription, context collection, transformation, and UI responsibilities separated so that providers and work modes can evolve independently.

## Runtime flow

```text
global shortcut / file input
        |
        v
audio capture or media extraction
        |
        v
speech-to-text provider
        |
        +--> optional project and active-window context
        |
        v
selected work mode transformation
        |
        v
HUD result -> clipboard / active input / TXT / SRT
```

## Main modules

| Module | Responsibility |
|---|---|
| `dikte_windows.py` | Desktop lifecycle, global shortcut, tray, capture, and HUD orchestration |
| `api.py` | Provider-facing transcription and text-processing calls |
| `filetranscribe.py` | Media transcription, chunking, progress, and export |
| `project_context.py` | Bounded collection of active project context |
| `work_modes.py` | Prompt and behavior contracts for each work mode |
| `config.py` | Local configuration and provider selection |
| `i18n.py` | Azerbaijani, English, and Turkish UI strings |

## Trust boundaries

- Microphone audio and selected media leave the device only when sent to the configured provider.
- Project context is collected locally and should be reviewed before using it with sensitive repositories.
- API credentials remain local and must never be committed or included in logs.
- Provider responses are untrusted input until reviewed by the user.

## Design constraints

- Windows 10 and 11 are the supported operating systems.
- The HUD must never block the user's active application longer than necessary.
- Provider failures must leave the original clipboard and input state recoverable.
- File transcription must preserve chunk order and export deterministic timestamps.
