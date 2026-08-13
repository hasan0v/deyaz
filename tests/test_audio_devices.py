"""Audio device selector behavior without requiring live Windows hardware."""

import unittest

from audio_devices import (
    audio_choice_signature, choice_index, resolve_sounddevice_selector,
    soundcard_microphone_choices, sounddevice_input_choices,
)


class FakeSoundDevice:
    class default:
        device = (1, 4)

    @staticmethod
    def query_devices():
        return [
            {"name": "Microsoft Sound Mapper - Input", "max_input_channels": 2, "hostapi": 0},
            {"name": "Laptop Mic", "max_input_channels": 2, "hostapi": 0},
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 0},
            {"name": "Primary Sound Capture Driver", "max_input_channels": 2, "hostapi": 1},
            {"name": "Laptop Mic", "max_input_channels": 2, "hostapi": 1},
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 1},
            {"name": "Laptop Mic", "max_input_channels": 2, "hostapi": 2},
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 2},
        ]

    @staticmethod
    def query_hostapis():
        return [
            {"name": "MME"},
            {"name": "Windows DirectSound"},
            {"name": "Windows WASAPI"},
        ]


class FakeMicrophone:
    def __init__(self, name, device_id):
        self.name = name
        self.id = device_id


class FakeSoundCard:
    @staticmethod
    def default_microphone():
        return FakeMicrophone("Laptop Mic", "mic-default")

    @staticmethod
    def all_microphones(include_loopback=False):
        assert include_loopback is False
        return [
            FakeMicrophone("Laptop Mic", "mic-default"),
            FakeMicrophone("USB Mic", "mic-usb"),
        ]


class AudioDeviceTests(unittest.TestCase):
    def test_sounddevice_choices_are_unambiguous_and_input_only(self):
        choices = sounddevice_input_choices(FakeSoundDevice)
        self.assertEqual(choices[0].value, "")
        self.assertEqual(choices[0].label, "Windows standart mikrofonu")
        self.assertEqual([choice.value for choice in choices[1:]], ["sd:6", "sd:7"])
        self.assertEqual([choice.label for choice in choices[1:]], ["Laptop Mic", "USB Mic"])
        self.assertNotIn("MME", " ".join(choice.label for choice in choices))
        self.assertNotIn("DirectSound", " ".join(choice.label for choice in choices))

    def test_legacy_microphone_name_is_migrated_to_concrete_selector(self):
        choices = sounddevice_input_choices(FakeSoundDevice)
        self.assertEqual(choice_index(choices, "USB Mic"), 2)
        self.assertEqual(choice_index(choices, "sd:1"), 1)
        self.assertEqual(choice_index(choices, "sd:4"), 1)
        self.assertEqual(choice_index(choices, "missing"), 0)

    def test_soundcard_choices_exclude_loopback_and_store_endpoint_id(self):
        choices = soundcard_microphone_choices(FakeSoundCard)
        self.assertEqual(choices[0].value, "")
        self.assertEqual(choices[2].value, "mic-usb")
        self.assertEqual(choice_index(choices, "USB Mic"), 2)

    def test_sounddevice_selector_is_converted_for_portaudio(self):
        self.assertEqual(resolve_sounddevice_selector("sd:12"), 12)
        self.assertEqual(resolve_sounddevice_selector("Legacy Mic"), "Legacy Mic")
        self.assertIsNone(resolve_sounddevice_selector(""))
        self.assertIsNone(resolve_sounddevice_selector("sd:nope"))

    def test_hotplug_signature_changes_when_microphone_is_added(self):
        before = sounddevice_input_choices(FakeSoundDevice)
        after = before + [type(before[0])("New USB Mic", "sd:9", "New USB Mic")]
        self.assertNotEqual(
            audio_choice_signature(before), audio_choice_signature(after)
        )


if __name__ == "__main__":
    unittest.main()
