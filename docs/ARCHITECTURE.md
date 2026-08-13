# Architecture

DeYaz is a native cross-platform desktop application. It keeps capture, transcription, context collection, transformation, and UI responsibilities separated so that providers, platforms and work modes can evolve independently.

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
| `deyaz_app.py` | Desktop lifecycle, global shortcut, tray, capture, and HUD orchestration |
| `api.py` | Provider-facing transcription and text-processing calls |
| `filetranscribe.py` | Media transcription, chunking, progress, and export |
| `project_context.py` | Bounded collection of active project context |
| `work_modes.py` | Prompt and behavior contracts for each work mode |
| `config.py` | Local configuration and provider selection |
| `i18n.py` | Azerbaijani, English, Turkish, and Russian UI strings |

## Active project detection

DeYaz does not trust an editor name or a window title as a stack detector. When
the global shortcut is pressed, it starts from the foreground process where the
platform exposes that information
and scores project roots evidenced by:

- the foreground process and its working directory;
- child terminal/agent processes and their working directories;
- parent launcher processes;
- existing paths explicitly present in process command lines;
- a project-name match in the active window title;
- repository markers such as `.git`, `package.json`, `pyproject.toml`,
  `requirements.txt`, solution files and common build manifests.

This covers Codex Desktop, VS Code, Cursor, Windsurf, Claude Code inside a
terminal, Visual Studio and JetBrains-style editor process trees without making
their presence a hard dependency. If several related project roots are open and
the active one cannot be distinguished, detection is marked ambiguous and its
context is not sent. The user-selected fallback directory remains the explicit
override.

For a verified root, context reads a bounded subset of non-secret project
metadata: README, AGENTS.md, package/requirements manifests, top-level names and
the current Git branch. Source files, `.env` files, credentials and arbitrary
workspace contents are not scanned.

## Trust boundaries

- Microphone audio and selected media leave the device only when sent to the configured provider.
- Project context is collected locally and should be reviewed before using it with sensitive repositories.
- README and manifest text is data, never an instruction; prompt rules explicitly
  prevent project files from overriding the user's request.
- API credentials remain local and must never be committed or included in logs.
- Provider responses are untrusted input until reviewed by the user.

## Design constraints

- Windows 10/11 is the full-featured reference platform.
- macOS and Linux support dictation, file transcription and mic-only meetings;
  global input permissions and Wayland restrictions are surfaced instead of hidden.
- The HUD must never block the user's active application longer than necessary.
- Provider failures must leave the original clipboard and input state recoverable.
- File transcription must preserve chunk order and export deterministic timestamps.
