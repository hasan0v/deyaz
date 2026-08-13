# Changelog

## 1.0.1 — Creator links and dictation polish

- Added responsive GitHub and LinkedIn creator actions beside the DeYaz wordmark.
- Added a restrained glow animation that highlights repository starring without disrupting the header.
- Removed the duplicate microphone icon from the dictation device selector.
- Moved the recorder glow to the full card and expanded its gutter so the right edge remains visible.

## 1.0.0 — First stable DeYaz release

- Localized default Meeting Notes speaker labels in AZ, EN, TR and RU.
- Fixed long context cards forcing their parent columns outside the visible area.
- Reset the public version line: stable starts at `1.0.0`; previews use `0.x`.

## 0.5.0 — Responsive UI and English documentation

- Added a shared responsive content-width system for the shell and primary pages.
- Improved header, navigation, home, dictation, file, meeting and settings layouts from 500 px to wide desktop sizes.
- Made context, add-context, model onboarding and custom work-mode dialogs adapt to narrow windows.
- Fixed narrow Meeting Notes control overlap and contained its dropdown arrow.
- Rewrote the main README in English with current features, platform notes, setup, privacy and development guidance.

## 0.4.0 — Context manager redesign

- Replaced native project radio controls with clear, checkable project cards.
- Project selection now supports zero or one folder and click-to-unselect.
- Text and file references remain independently selectable.
- Added scrollable panels, stronger interaction states and corrected dark-mode contrast.

## 0.3.0 — UI clipping fixes

- Dictation mode dropdown arrow stays inside the rounded selector.
- Dictation microphone and record cards keep their full right border and shadow.
- File player seek handle has enough vertical space and is no longer cropped.

## 0.2.0 — Localization and context fixes

- File transcription custom focus is always editable and is applied when supplied.
- Settings, file and meeting labels are fully localized in AZ, EN, TR and RU.
- Context selection now allows one project via radio buttons and multiple text/file references via checkboxes.
- Result panels have clear actions; the dictation result starts empty after every app restart while history remains available.
- Windows FFmpeg helpers run without flashing a terminal window.
- Narrow-window home cards no longer crop localized labels.

## 0.1.0 — DeYaz preview

- Tətbiq və paketlər `Dikte`-dən `DeYaz`-a rebrand edildi.
- Yeni pastel neo-brutalist D monogramı PNG, SVG, ICO və ICNS kimi əlavə edildi.
- Köhnə config, history, meeting və credential məlumatları üçün silməyən migrasiya quruldu.
- OpenAI və OpenRouter açarları OS credential store/keychain-ə köçürüldü.
- Windows, macOS və Linux üçün native GitHub Actions build/release matrix-i əlavə edildi.
- macOS/Linux qlobal shortcut və smart paste üçün platform fallback-i əlavə edildi.
- Meeting Notes Windows-da mic + system audio, digər platformalarda mic-only işləyir.
- Diktə, file transcript, canlı meeting, lokalizasiya, model və media regression testləri genişləndirildi.
