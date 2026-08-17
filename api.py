"""OpenAI and OpenRouter calls, stdlib only.

Transcription runs on either provider: OpenRouter mirrors OpenAI's
/audio/transcriptions endpoint field for field, so one multipart request serves
both and only the key, the base URL and the model id change. Cleanup is always
OpenRouter.
"""

import collections
import json
import logging
import mimetypes
import os
import re
import secrets
import time
import urllib.error
import urllib.request
import wave

from i18n import t

logger = logging.getLogger("deyaz.api")

APP_URL = "https://github.com/hasan0v/deyaz"
USER_AGENT = f"deyaz/1.0.12 (+{APP_URL})"
OPENAI_URL = "https://api.openai.com/v1"
OPENROUTER_URL = "https://openrouter.ai/api/v1"

# Where a transcription request goes; built by config.Config.transcribe_target().
# `service` is the name the user sees in an error, `provider` the one the code
# branches on.
Target = collections.namedtuple("Target", "provider service api_key base_url model")


class ApiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class EmptyTranscriptError(ApiError):
    """The request succeeded, but the model detected no transcribable speech."""


def explain(exc, service):
    """Turn an HTTP status into something the user can act on."""
    if exc.status in (401, 402, 403) and "provider returned" in str(exc).lower():
        return ApiError(
            t("The selected model provider cannot currently process this "
              "OpenRouter request (HTTP {code}). Choose another transcription model.",
              code=exc.status),
            exc.status,
        )
    if exc.status in (401, 403):
        return ApiError(t("{service} rejected the API key (HTTP {code}). Open "
                          "Settings and check it.", service=service, code=exc.status),
                        exc.status)
    if exc.status == 402:
        return ApiError(t("{service} says the account is out of credit (HTTP 402).",
                          service=service), exc.status)
    if exc.status == 429:
        return ApiError(t("{service} is rate limiting you (HTTP 429). Try again in "
                          "a moment.", service=service), exc.status)
    return ApiError(f"{service}: {exc}", exc.status)


def _request(url, data, headers, timeout=120):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise ApiError(f"HTTP {exc.code}: {_extract_error(body)}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise ApiError(t("Could not connect: {reason}", reason=exc.reason)) from exc
    except TimeoutError as exc:
        raise ApiError(t("Could not connect: request timed out")) from exc
    except json.JSONDecodeError as exc:
        raise ApiError(t("Could not parse the response: {error}", error=exc)) from exc


def _extract_error(body):
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:300]
    err = payload.get("error")
    if isinstance(err, dict):
        return err.get("message") or json.dumps(err)[:300]
    if isinstance(err, str):
        return err
    return body[:300]


def _multipart(fields, file_field, file_path):
    """Build a multipart/form-data body; returns (body, content-type)."""
    boundary = "----deyaz" + secrets.token_hex(16)
    out = bytearray()
    for name, value in fields:
        if value is None or value == "":
            continue
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += str(value).encode("utf-8") + b"\r\n"

    filename = os.path.basename(file_path)
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with open(file_path, "rb") as fh:
        payload = fh.read()
    out += f"--{boundary}\r\n".encode()
    out += (
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode()
    out += payload + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _headers(provider, api_key, content_type=None):
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT}
    if content_type:
        headers["Content-Type"] = content_type
    if provider == "openrouter":
        # What OpenRouter attributes the calls to on its app leaderboard.
        headers["HTTP-Referer"] = APP_URL
        headers["X-Title"] = "DeYaz"
    return headers


def _transcribe_request(target, wav_path, language, prompt, response_format,
                        granularity=None, timeout=300):
    if not target.api_key:
        raise ApiError(t("{service} API key is empty. Add it in Settings.",
                         service=target.service))
    fields = [("model", target.model), ("response_format", response_format)]
    if language and language != "auto":
        fields.append(("language", language))
    # OpenRouter takes the hint field and throws it away, so spare it the bytes.
    # The same words still reach the cleanup model as a glossary.
    if prompt and target.provider == "openai":
        fields.append(("prompt", prompt))
    if granularity:
        fields.append(("timestamp_granularities[]", granularity))
    body, ctype = _multipart(fields, "file", wav_path)
    started = time.monotonic()
    file_bytes = os.path.getsize(wav_path) if os.path.exists(wav_path) else -1
    logger.info(
        "transcription_request provider=%s model=%s format=%s bytes=%s language=%s",
        target.provider, target.model, response_format, file_bytes,
        language or "auto",
    )
    try:
        result = _request(
            f"{target.base_url.rstrip('/')}/audio/transcriptions", body,
            _headers(target.provider, target.api_key, ctype), timeout=timeout,
        )
        logger.info(
            "transcription_response provider=%s model=%s format=%s elapsed_ms=%d has_text=%s",
            target.provider, target.model, response_format,
            int((time.monotonic() - started) * 1000), bool(result.get("text")),
        )
        return result
    except ApiError as exc:
        logger.warning(
            "transcription_error provider=%s model=%s format=%s status=%s elapsed_ms=%d error=%s",
            target.provider, target.model, response_format, exc.status,
            int((time.monotonic() - started) * 1000), str(exc),
        )
        raise explain(exc, target.service) from None


def transcribe(target, wav_path, language="", prompt="", timeout=300):
    data = _transcribe_request(
        target, wav_path, language, prompt, "json", timeout=timeout
    )
    text = (data.get("text") or "").strip()
    if not text:
        # Transcription providers occasionally acknowledge an audio request
        # with usage metadata but an empty text field. One retry is safer than
        # presenting that transient response as a finished empty transcript.
        data = _transcribe_request(
            target, wav_path, language, prompt, "json", timeout=timeout
        )
        text = (data.get("text") or "").strip()
    if not text:
        raise EmptyTranscriptError(t("Transcript came back empty."))
    return text


def transcribe_segments(target, wav_path, language="", prompt="", timeout=300):
    """Return timed captions while always preserving the selected model.

    Models that support ``verbose_json`` return their native segment timing.
    JSON-only models fall back to evenly timed, sentence-aware captions made
    from that same model's transcript; DeYaz never swaps in a legacy model.
    """
    model_name = (target.model or "").split("/")[-1]
    json_only = model_name in {
        "gpt-transcribe", "gpt-4o-transcribe", "gpt-4o-mini-transcribe",
        "gpt-4o-transcribe-diarize",
    }
    if not json_only:
        try:
            data = _transcribe_request(
                target, wav_path, language, prompt, "verbose_json",
                granularity="segment", timeout=timeout,
            )
            native = []
            for item in data.get("segments") or []:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                try:
                    start = max(0.0, float(item.get("start") or 0.0))
                    end = max(start, float(item.get("end") or start))
                except (TypeError, ValueError):
                    continue
                native.append((start, end, text))
            if native:
                return native
            full_text = str(data.get("text") or "").strip()
            if full_text:
                return timed_caption_segments(full_text, _wav_duration(wav_path))
        except ApiError as exc:
            # A provider may expose the model but not verbose timestamps. Retry
            # the same model in JSON mode instead of silently substituting one.
            if exc.status not in (400, 404, 415, 422):
                raise

    text = transcribe(
        target, wav_path, language=language, prompt=prompt, timeout=timeout
    )
    return timed_caption_segments(text, _wav_duration(wav_path))


def _wav_duration(path):
    try:
        with wave.open(path, "rb") as source:
            rate = source.getframerate()
            return source.getnframes() / float(rate) if rate else 0.0
    except (OSError, wave.Error):
        return 0.0


def _caption_chunks(text, max_chars=96):
    """Split prose at sentence boundaries, then wrap unusually long lines."""
    sentences = re.split(r"(?<=[.!?…])\s+", " ".join(str(text).split()))
    chunks = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) <= max_chars:
            current = f"{current} {sentence}"
            continue
        if current:
            chunks.append(current)
            current = ""
        words = sentence.split()
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if line and len(candidate) > max_chars:
                chunks.append(line)
                line = word
            else:
                line = candidate
        current = line
    if current:
        chunks.append(current)
    return chunks


def timed_caption_segments(text, duration, fallback_seconds=4.0):
    """Create readable proportional timings for a transcript without metadata."""
    chunks = _caption_chunks(text)
    if not chunks:
        return []
    duration = max(0.0, float(duration or 0.0))
    if duration <= 0:
        return [
            (index * fallback_seconds, (index + 1) * fallback_seconds, chunk)
            for index, chunk in enumerate(chunks)
        ]
    weights = [max(1, len(chunk.split())) for chunk in chunks]
    total = float(sum(weights))
    elapsed = 0.0
    out = []
    for index, (chunk, weight) in enumerate(zip(chunks, weights)):
        start = elapsed
        elapsed = duration if index == len(chunks) - 1 else min(
            duration, elapsed + duration * weight / total
        )
        out.append((start, max(start + 0.05, elapsed), chunk))
    return out


def cleanup(text, api_key, model, system_prompt, reasoning="",
            base_url=OPENROUTER_URL, timeout=180, context="",
            provider="openrouter", service="OpenRouter"):
    if not api_key:
        raise ApiError(t("{service} API key is empty. Add it in Settings.",
                         service="OpenRouter"))
    user_content = f"<transcript>\n{text}\n</transcript>"
    if context:
        user_content = (
            f"<project_context>\n{context}\n</project_context>\n\n"
            f"{user_content}"
        )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    # GPT-5.6 on OpenAI accepts only its default temperature. OpenRouter can
    # normalize this parameter for routed models, but the direct API correctly
    # rejects an explicit zero.
    if not (provider == "openai" and model.startswith("gpt-5.6")):
        payload["temperature"] = 0
    # An empty level means "whatever the model does on its own"; anything else is
    # one of OpenRouter's efforts. The thinking itself is never shown, so ask for
    # it to be left out of the reply.
    if reasoning:
        payload["reasoning"] = {"effort": reasoning, "exclude": True}
    try:
        data = _request(
            f"{base_url.rstrip('/')}/chat/completions",
            json.dumps(payload).encode("utf-8"),
            _headers(provider, api_key, "application/json"),
            timeout=timeout,
        )
    except ApiError as exc:
        raise explain(exc, service) from None
    choices = data.get("choices") or []
    if not choices:
        raise ApiError(_extract_error(json.dumps(data)))
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise ApiError(t("The cleanup model returned an empty reply."))
    return content


def chat(messages, api_key, model, system_prompt, reasoning="",
         base_url=OPENROUTER_URL, timeout=180):
    """A conversation, rather than one transcript rewritten.

    The messages are the whole history and come back unchanged; the caller keeps
    them, because there is no session on OpenRouter's side to resume.
    """
    if not api_key:
        raise ApiError(t("{service} API key is empty. Add it in Settings.",
                         service="OpenRouter"))
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + list(messages),
    }
    if reasoning:
        payload["reasoning"] = {"effort": reasoning, "exclude": True}
    try:
        data = _request(
            f"{base_url.rstrip('/')}/chat/completions",
            json.dumps(payload).encode("utf-8"),
            _headers("openrouter", api_key, "application/json"),
            timeout=timeout,
        )
    except ApiError as exc:
        raise explain(exc, "OpenRouter") from None
    choices = data.get("choices") or []
    if not choices:
        raise ApiError(_extract_error(json.dumps(data)))
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise ApiError(t("The model returned an empty reply."))
    return content


def _get_json(url, headers, timeout=20):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise ApiError(f"HTTP {exc.code}: {_extract_error(body)}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise ApiError(t("Could not connect: {reason}", reason=exc.reason)) from exc
    except json.JSONDecodeError as exc:
        raise ApiError(t("Could not parse the response: {error}", error=exc)) from exc


def openrouter_key_status(api_key):
    """Check the key against OpenRouter's own /key endpoint."""
    if not api_key:
        raise ApiError(t("{service} API key is empty. Add it in Settings.",
                         service="OpenRouter"))
    info = openrouter_key_info(api_key)
    limit, usage = info.get("limit"), info.get("usage")
    if limit is None:
        return t("Key works, no spending limit set.")
    return t("Key works. Used {usage} of {limit}.",
             usage=round(float(usage or 0), 3), limit=round(float(limit), 3))


def openrouter_key_info(api_key):
    """Return key limits without exposing the credential itself."""
    if not api_key:
        raise ApiError(t("{service} API key is empty. Add it in Settings.",
                         service="OpenRouter"))
    try:
        data = _get_json(
            f"{OPENROUTER_URL}/key",
            {"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
        )
    except ApiError as exc:
        raise explain(exc, "OpenRouter") from None
    return data.get("data") or {}


def openrouter_account_info(api_key):
    """Return key limits plus the real account credit balance."""
    info = dict(openrouter_key_info(api_key))
    try:
        data = _get_json(
            f"{OPENROUTER_URL}/credits",
            {"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
        ).get("data") or {}
        total_credits = float(data.get("total_credits") or 0)
        total_usage = float(data.get("total_usage") or 0)
        info["total_credits"] = total_credits
        info["total_usage"] = total_usage
        info["account_balance"] = max(0.0, total_credits - total_usage)
    except ApiError:
        # A valid key result is still useful if the credits endpoint is
        # temporarily unavailable.
        pass
    return info


def openrouter_models(api_key="", transcription=False):
    """Model ids available on OpenRouter (no key required).

    `transcription` narrows the list to the speech-to-text models, the only ones
    /audio/transcriptions accepts. The filter is applied again on the result,
    because a query parameter the API stops honouring would otherwise quietly
    hand back all several hundred models.
    """
    url = f"{OPENROUTER_URL}/models"
    if transcription:
        url += "?output_modalities=transcription"
    headers = {"User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    models = _get_json(url, headers).get("data", [])
    if transcription:
        models = [m for m in models
                  if "transcription" in (m.get("architecture") or {}).get(
                      "output_modalities", [])]
    return sorted(m["id"] for m in models if m.get("id"))


def openai_models(api_key, base_url=OPENAI_URL, transcription=True):
    if not api_key:
        raise ApiError(t("{service} API key is empty. Add it in Settings.",
                         service="OpenAI"))
    try:
        data = _get_json(
            f"{base_url.rstrip('/')}/models",
            {"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
        )
    except ApiError as exc:
        raise explain(exc, "OpenAI") from None
    ids = [m["id"] for m in data.get("data", []) if m.get("id")]
    if not transcription:
        return sorted(ids)
    audio = [i for i in ids if "transcribe" in i or "whisper" in i]
    return sorted(audio or ids)
