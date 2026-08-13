"""Stable, user-facing audio input choices for DeYaz desktop."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioDeviceChoice:
    label: str
    value: str
    name: str
    aliases: tuple[str, ...] = ()


def _default_input_index(sounddevice_module):
    try:
        value = sounddevice_module.default.device
        return int(value[0] if isinstance(value, (tuple, list)) else value)
    except (AttributeError, IndexError, TypeError, ValueError):
        return -1


def sounddevice_input_choices(sounddevice_module):
    """Return one friendly entry per Windows microphone.

    PortAudio exposes the same physical device through MME, DirectSound,
    WASAPI and low-level WDM-KS endpoints. Prefer one modern host API for the
    whole list and retain the hidden duplicate indexes as migration aliases.
    """
    try:
        devices = list(sounddevice_module.query_devices())
    except Exception:
        devices = []
    try:
        hostapis = list(sounddevice_module.query_hostapis())
    except Exception:
        hostapis = []

    default_index = _default_input_index(sounddevice_module)
    default_name = ""
    if 0 <= default_index < len(devices):
        default_name = str(devices[default_index].get("name", "")).strip()
    choices = [AudioDeviceChoice("Windows standart mikrofonu", "", default_name)]

    inputs = []
    for index, device in enumerate(devices):
        try:
            if int(device.get("max_input_channels", 0)) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        name = str(device.get("name", "")).strip()
        if not name:
            continue
        host_name = ""
        try:
            host_index = int(device.get("hostapi", -1))
            if 0 <= host_index < len(hostapis):
                host_name = str(hostapis[host_index].get("name", "")).strip()
        except (TypeError, ValueError):
            pass
        inputs.append((index, name, host_name))

    # WASAPI gives the cleanest modern Windows endpoints. If it is unavailable,
    # fall back once to DirectSound, then MME; never mix the backends in the UI.
    host_order = ("wasapi", "directsound", "mme")
    selected_inputs = []
    for preferred in host_order:
        selected_inputs = [
            item for item in inputs if preferred in item[2].casefold()
        ]
        if selected_inputs:
            break
    if not selected_inputs:
        selected_inputs = [
            item for item in inputs if "wdm-ks" not in item[2].casefold()
        ]

    generic_names = {
        "microsoft sound mapper - input",
        "primary sound capture driver",
        "input ()",
        "microphone ()",
    }
    seen_names = set()
    for index, name, _host_name in selected_inputs:
        folded = " ".join(name.casefold().split())
        if folded in generic_names or folded in seen_names:
            continue
        seen_names.add(folded)
        aliases = []
        for other_index, other_name, _other_host in inputs:
            other_folded = " ".join(other_name.casefold().split())
            if other_folded == folded:
                aliases.append(f"sd:{other_index}")
        choices.append(AudioDeviceChoice(
            name, f"sd:{index}", name, tuple(aliases),
        ))
    return choices


def soundcard_microphone_choices(soundcard_module):
    """Return physical microphone endpoints; loopback speakers are excluded."""
    try:
        default = soundcard_module.default_microphone()
    except Exception:
        default = None
    default_name = str(getattr(default, "name", "") or "").strip()
    default_label = "Windows standart mikrofonu"
    if default_name:
        default_label += f" · {default_name}"
    choices = [AudioDeviceChoice(default_label, "", default_name)]

    try:
        microphones = soundcard_module.all_microphones(include_loopback=False)
    except Exception:
        microphones = []
    seen = set()
    for microphone in microphones:
        name = str(getattr(microphone, "name", "") or "").strip()
        device_id = str(getattr(microphone, "id", "") or "").strip()
        if not name or not device_id or device_id in seen:
            continue
        seen.add(device_id)
        choices.append(AudioDeviceChoice(name, device_id, name))
    return choices


def choice_index(choices, saved_value):
    """Find a stored selector and migrate legacy name-only settings."""
    saved = str(saved_value or "").strip()
    if not saved:
        return 0
    for index, choice in enumerate(choices):
        if choice.value == saved:
            return index
        if saved in choice.aliases:
            return index
    saved_folded = saved.casefold()
    for index, choice in enumerate(choices):
        if choice.name.casefold() == saved_folded:
            return index
    return 0


def resolve_sounddevice_selector(selector):
    """Convert a stored ``sd:N`` selector to the value PortAudio expects."""
    value = str(selector or "").strip()
    if value.startswith("sd:"):
        try:
            return int(value[3:])
        except ValueError:
            return None
    return value or None


def audio_choice_signature(choices):
    """Stable identity used to detect Windows audio hot-plug changes."""
    return tuple((choice.value, choice.label) for choice in choices)
