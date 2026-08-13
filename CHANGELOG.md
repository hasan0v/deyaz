# Changelog

## 1.0.7 — History stability and branded setup

- Fixed the crash triggered by opening the history drawer.
- Added DeYaz artwork, creator details and product messaging to the Windows installer.
- Replaced the generic setup artwork with DeYaz-branded wizard assets.

## 1.0.6 — Installer deadlock fix

- Removed the legacy-app wait that could freeze the installer on Preparing.
- Kept the update close step hidden, immediate and compatible with 1.0.3+.

## 1.0.5 — Seamless Windows updates

- Added a private shutdown command for installer-driven updates.
- Closed legacy background instances before replacing installed files.
- Removed the confusing files-in-use step from normal DeYaz upgrades.

## 1.0.4 — Dictation workspace polish

- Added direct transcription and text model selectors to Dictation mode.
- Kept the result workspace stable when clearing generated text.
- Added lightweight preparing, tab activation, and page transition motion.
- Changed the LinkedIn call to action to first-person copy.

## 1.0.3 — Native installed runtime

- Changed the installed Windows runtime from PyInstaller one-file to one-folder.
- Removed the visible parent/child process duplication during installer updates.
- Improved startup time and further reduced heuristic antivirus risk.

## 1.0.2 — Windows installer and motion polish

- Added a proper per-user Windows installer with Start Menu and uninstall registration.
- Removed UPX packing to reduce heuristic antivirus false positives.
- Matched the GitHub button to the header and moved attention to a softly glowing star.
- Added restrained, layout-safe transitions between the main work surfaces.

## 1.0.1 — Creator links and dictation polish

- Added responsive GitHub and LinkedIn creator actions beside the DeYaz wordmark.
- Added a restrained glow animation that highlights repository starring without disrupting the header.
- Removed the duplicate microphone icon from the dictation device selector.
- Moved the recorder glow to the full card and expanded its gutter so the right edge remains visible.

## 1.0.0 — Localization and context stability

- Localized default Meeting Notes speaker labels in AZ, EN, TR and RU.
- Fixed long context cards forcing their parent columns outside the visible area.

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
