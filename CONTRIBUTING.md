# Contributing

Contributions that improve reliability, accessibility, language support, documentation, or Windows/macOS/Linux compatibility are welcome.

## Development workflow

1. Open an issue describing the problem and expected behavior.
2. Create a focused branch from `main`.
3. Keep provider-specific logic behind the existing API boundary.
4. Run `python -m py_compile deyaz_app.py api.py config.py filetranscribe.py i18n.py project_context.py work_modes.py`.
5. Confirm that no credentials, recordings, transcripts, or private project context are included.
6. Open a pull request with validation steps and screenshots for visible UI changes.

Platform-specific changes should preserve graceful fallback behavior on the other
two desktop platforms. Never commit API keys, transcripts or sample recordings.

By contributing, you agree that your contribution is licensed under GPL-3.0.
