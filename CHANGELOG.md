# Changelog

## 3.0.1 — Localization and context fixes

- File transcription custom focus is always editable and is applied when supplied.
- Settings, file and meeting labels are fully localized in AZ, EN, TR and RU.
- Context selection now allows one project via radio buttons and multiple text/file references via checkboxes.
- Result panels have clear actions; the dictation result starts empty after every app restart while history remains available.
- Windows FFmpeg helpers run without flashing a terminal window.
- Narrow-window home cards no longer crop localized labels.

## 3.0.0 — DeYaz

- Tətbiq və paketlər `Dikte`-dən `DeYaz`-a rebrand edildi.
- Yeni pastel neo-brutalist D monogramı PNG, SVG, ICO və ICNS kimi əlavə edildi.
- Köhnə config, history, meeting və credential məlumatları üçün silməyən migrasiya quruldu.
- OpenAI və OpenRouter açarları OS credential store/keychain-ə köçürüldü.
- Windows, macOS və Linux üçün native GitHub Actions build/release matrix-i əlavə edildi.
- macOS/Linux qlobal shortcut və smart paste üçün platform fallback-i əlavə edildi.
- Meeting Notes Windows-da mic + system audio, digər platformalarda mic-only işləyir.
- Diktə, file transcript, canlı meeting, lokalizasiya, model və media regression testləri genişləndirildi.
