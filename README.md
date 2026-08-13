# DeYaz

<p align="center">
  <img src="assets/deyaz-logo.png" width="144" alt="DeYaz logo">
</p>

<p align="center">
  A native, open-source desktop workspace for dictation, media transcription,
  and live meeting notes.
</p>

DeYaz turns microphone input, audio/video files, and meetings into useful text.
It is built with PyQt6, keeps each workflow independent, and provides a localized
interface in Azerbaijani, English, Turkish, and Russian.

## Features

### Dictation

- Start or stop recording from the dashboard, global shortcut, or floating mini button.
- Insert the result into the currently focused application with Smart Paste.
- Apply purpose-specific work modes such as plain dictation or prompt refinement.
- Optionally attach one project folder and multiple text/file references as context.
- Never invent a project stack when DeYaz cannot verify it from available evidence.

### File transcription

- Import common audio and video formats.
- Preview video inside the built-in player or play audio with an animated waveform.
- Seek backward and forward while reviewing the source.
- Produce a full transcript, cleaned transcript, summary, or focused result.
- Export TXT, SRT, and PDF output when supported by the selected result type.

### Meeting Notes

- Capture live microphone input and display partial transcription while people speak.
- Capture microphone and system audio together on Windows.
- Keep the live transcript separate from the processed meeting result.
- Generate a full transcript, summary, key points, detailed review, or action items.
- Choose the meeting language, output language, live transcription model, and text model independently.

### Context manager

- Select zero or one project folder at a time.
- Click the selected project again to remove it.
- Enable multiple pasted-text and file references independently.
- Read only a bounded set of project metadata; source trees, `.env` files, and secrets are not scanned.

### Desktop experience

- Responsive pastel neo-brutalist UI with light and dark themes.
- Azerbaijani, English, Turkish, and Russian localization.
- System tray controls, draggable floating recorder, microphone hot-plug refresh, and local history.
- Separate OpenAI and OpenRouter account/model configuration.

## Platform support

| Platform | Package | Support notes |
|---|---|---|
| Windows 10/11 x64 | `DeYaz-Windows-x64.exe` | Signed installer when release signing secrets are configured; Start Menu and Installed apps integration |
| macOS 14+ Apple Silicon | `DeYaz-macOS-arm64.zip` | Dictation, file transcription, mic-only meetings; Accessibility and microphone permissions may be required |
| Linux x64 | `DeYaz-Linux-x64.tar.gz` | Dictation, file transcription, mic-only meetings; global shortcuts work best on X11 and may be restricted on Wayland |

The macOS build is currently unsigned. On first launch, macOS may require an
explicit **Open** confirmation. System-audio meeting capture on macOS and Linux
requires operating-system audio routing or a virtual audio device and is not
advertised as built-in support.

## Download

Download the latest native package from
[GitHub Releases](https://github.com/hasan0v/deyaz/releases).

Windows users should run the installer. It installs DeYaz per user, adds a Start
Menu shortcut, and registers a standard uninstaller in Windows Installed apps.
The application stores settings outside the executable, so installing a newer release
does not remove normal user configuration.

## Run from source

Requirements:

- Python 3.11 or newer; CI and release builds use Python 3.13
- A working microphone for dictation or meeting capture
- An OpenAI API key, an OpenRouter account/key, or both, depending on the selected models

```bash
git clone https://github.com/hasan0v/deyaz.git
cd deyaz
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python deyaz_app.py
```

macOS or Linux:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python deyaz_app.py
```

Linux may also require system packages for EGL/OpenGL, PulseAudio, and PortAudio.
The release workflow installs `libegl1`, `libgl1`, `libpulse0`, and `libportaudio2`
on Ubuntu.

## Provider configuration

Open **Settings** in DeYaz and connect the provider required by the models you want
to use. Dictation, file transcription, meeting transcription, and text processing
keep their own model choices where the workflow requires it.

- OpenAI speech models require an OpenAI API key and OpenAI billing.
- OpenRouter speech models use an OpenRouter key/account and OpenRouter credit.
- OpenRouter text processing can use paid models or the available free route.
- True-live meeting transcription uses a compatible OpenAI realtime transcription model.

The UI disables primary actions when the required provider is not configured and
directs the user to Settings.

## Shortcuts and background use

- The default recording shortcut is `Ctrl+Alt+R` on Windows and can be changed in Settings.
- Closing the main window minimizes DeYaz to its floating mini recorder/system tray instead of ending the background service.
- Use the tray or mini-recorder menu to reopen or fully quit the application.

Global shortcuts and automatic paste require the relevant accessibility/input
permissions on macOS and compatible desktop-session support on Linux.

## Local data and privacy

| Platform | Configuration and application data |
|---|---|
| Windows | `%APPDATA%\DeYaz` and `%LOCALAPPDATA%\DeYaz` |
| macOS/Linux | `$XDG_CONFIG_HOME/deyaz` and `$XDG_DATA_HOME/deyaz` |

- Provider credentials are stored through the operating system credential store/keychain when available.
- Legacy Dikte settings are copied during migration; the old data is not deleted.
- Recordings are retained only when the relevant save option is enabled.
- Transcription history is stored locally.
- Audio, selected media, and enabled context leave the device only when sent to the configured provider.

Review [SECURITY.md](SECURITY.md) and the trust boundaries in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before using sensitive project context.

## Test and build

Run the test suite:

```bash
python -m pytest -q
```

Build for the current operating system:

```bash
python -m PyInstaller --noconfirm --clean DeYaz.spec
```

To build the standard Windows installer after installing Inno Setup 6:

```powershell
.\scripts\build_windows_installer.ps1
```

Release signing is optional in local builds. Official Windows releases should set
`WINDOWS_CERTIFICATE_BASE64` and `WINDOWS_CERTIFICATE_PASSWORD` GitHub secrets so
both the application and installer receive an Authenticode signature. No project
can guarantee zero antivirus warnings, but signed, unpacked binaries with stable
publisher metadata substantially reduce heuristic false positives.

The Windows installer uses PyInstaller's one-folder runtime internally. Users still
receive one setup file and one Start Menu entry, while DeYaz starts faster and does
not expose the one-file bootloader's parent/child process pair during updates.

PyInstaller is not a cross-compiler. The
[desktop build workflow](.github/workflows/build-desktop.yml) builds Windows,
macOS, and Linux packages on native GitHub runners. Pushing a `v*` tag publishes
the three artifacts as a GitHub Release.

## Project structure

| File | Responsibility |
|---|---|
| `deyaz_app.py` | PyQt6 UI, desktop lifecycle, capture orchestration, shortcut, tray, and floating recorder |
| `api.py` | OpenAI/OpenRouter transcription and text-processing requests |
| `filetranscribe.py` | Media extraction, transcription, progress, and export preparation |
| `meeting_capture.py` | Microphone and supported system-audio meeting capture |
| `realtime_transcription.py` | Low-latency live transcription transport |
| `project_context.py` | Bounded project detection and context collection |
| `work_modes.py` | Dictation work-mode contracts and prompts |
| `config.py` | Configuration, migration, and provider/model defaults |
| `credential_store.py` | Operating-system credential storage |
| `i18n.py` | AZ/EN/TR/RU interface catalog |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for runtime boundaries and
[docs/EVALUATION.md](docs/EVALUATION.md) for model evaluation guidance.

## Contributing

Contributions that improve reliability, accessibility, localization, responsive
behavior, or cross-platform support are welcome. Read
[CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Never include
API keys, private transcripts, recordings, or project context in commits or test
fixtures.

## License and attribution

DeYaz is licensed under **GPL-3.0-or-later**. The project began from
`yusufipk/dikte`; license and attribution details are documented in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
