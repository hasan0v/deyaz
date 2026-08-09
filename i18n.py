"""Small runtime translation helper for the native Windows application."""

import locale
import os


_language = "az"


AZ = {
    "{service} rejected the API key (HTTP {code}). Open Settings and check it.":
        "{service} API açarını qəbul etmədi (HTTP {code}). Ayarlarda açarı yoxla.",
    "{service} says the account is out of credit (HTTP 402).":
        "{service} hesabında kredit qalmayıb (HTTP 402).",
    "{service} is rate limiting you (HTTP 429). Try again in a moment.":
        "{service} sorğu limitini tətbiq edir (HTTP 429). Bir az sonra yenidən yoxla.",
    "Could not connect: {reason}": "Bağlantı qurulmadı: {reason}",
    "Could not parse the response: {error}": "Cavab oxuna bilmədi: {error}",
    "{service} API key is empty. Add it in Settings.":
        "{service} API açarı boşdur. Ayarlarda əlavə et.",
    "Transcript came back empty.": "Transkript boş qayıtdı.",
    "The cleanup model returned an empty reply.":
        "Təmizləmə modeli boş cavab qaytardı.",
    "The model returned an empty reply.": "Model boş cavab qaytardı.",
    "Key works, no spending limit set.": "Açar işləyir, xərc limiti təyin edilməyib.",
    "Key works. Used {usage} of {limit}.":
        "Açar işləyir. {limit} limitindən {usage} istifadə olunub.",
    "ffmpeg not found. Install it to transcribe files.":
        "FFmpeg tapılmadı. Faylları transkripsiya etmək üçün FFmpeg quraşdır.",
    "Converting audio…": "Audio hazırlanır…",
    "Splitting into {count} chunks…": "Fayl {count} hissəyə bölünür…",
    "Transcribing chunk {index}/{count}…":
        "Hissə transkripsiya olunur: {index}/{count}…",
    "Cleaning up…": "Mətn təmizlənir…",
    "Stopped.": "Dayandırıldı.",
    "Could not read the file: {error}": "Fayl oxuna bilmədi: {error}",
}


def resolve(code):
    if code in ("az", "en", "tr"):
        return code
    env = os.environ.get("LANG", "")
    system_locale = locale.getlocale()[0] or ""
    detected = (env or system_locale).lower()
    if detected.startswith("tr"):
        return "tr"
    if detected.startswith("en"):
        return "en"
    return "az"


def set_language(code):
    global _language
    _language = resolve(code)


def language():
    return _language


def t(text, **kwargs):
    translated = AZ.get(text, text) if _language == "az" else text
    return translated.format(**kwargs) if kwargs else translated
