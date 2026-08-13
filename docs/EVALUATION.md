# Evaluation plan

This document defines how DeYaz should be evaluated before a release. It intentionally does not publish benchmark numbers that have not been reproduced.

## Quality dimensions

| Dimension | Measurement |
|---|---|
| Transcription accuracy | Word error rate on a consented, redacted multilingual sample |
| Completion reliability | Successful capture-to-insertion runs divided by attempted runs |
| Interactive latency | Median and p95 time from stop-recording to result-ready |
| File handling | Successful exports across supported media formats and representative durations |
| Context safety | No unrelated file content included outside the configured project boundary |
| Recovery | Provider, microphone, and clipboard failures produce a clear recoverable state |

## Test matrix

- Windows 10/11, macOS 14+, and Ubuntu 22.04+
- Azerbaijani, English, Turkish, and mixed-language speech
- Short commands, long-form dictation, noisy audio, and silence
- OpenAI and OpenRouter provider configurations
- TXT and SRT export for every documented media type
- Active text fields in browsers, editors, office tools, and terminals

## Release gate

1. The Windows, macOS, and Linux build matrix completes.
2. Python source validation passes.
3. No credential, transcript, or project-content fixture is committed.
4. Critical capture, insertion, and export paths pass the manual matrix.
5. Known limitations are recorded in the release notes.

## Reporting results

When a benchmark is run, record the dataset origin, consent status, sample count, language mix, hardware, provider, model, date, and exact configuration. Results without this context should not be presented as product claims.
