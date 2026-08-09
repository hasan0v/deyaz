# Contributing

Contributions that improve reliability, accessibility, language support, documentation, or Windows compatibility are welcome.

## Development workflow

1. Open an issue describing the problem and expected behavior.
2. Create a focused branch from `main`.
3. Keep provider-specific logic behind the existing API boundary.
4. Run `python -m py_compile dikte_windows.py api.py config.py filetranscribe.py i18n.py project_context.py work_modes.py`.
5. Confirm that no credentials, recordings, transcripts, or private project context are included.
6. Open a pull request with validation steps and screenshots for visible UI changes.

By contributing, you agree that your contribution is licensed under GPL-3.0.
