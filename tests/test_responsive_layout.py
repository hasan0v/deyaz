import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from deyaz_app import (
    ContextAddDialog, ContextManagerDialog, ModelOnboardingDialog,
    OPENROUTER_CLEANUP_CHOICES, OPENROUTER_TRANSCRIPTION_CHOICES,
    meeting_layout_mode_for_width, responsive_content_width,
    responsive_density_for_width,
)


class ResponsiveMeetingLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_standard_small_window_uses_two_column_split(self):
        self.assertEqual(meeting_layout_mode_for_width(900), "split")

    def test_phone_width_stacks_panels(self):
        self.assertEqual(meeting_layout_mode_for_width(640), "stack")

    def test_wide_window_keeps_three_columns(self):
        self.assertEqual(meeting_layout_mode_for_width(1600), "wide")

    def test_shell_density_and_real_content_width_share_breakpoints(self):
        self.assertEqual(responsive_density_for_width(500), "narrow")
        self.assertEqual(responsive_density_for_width(800), "compact")
        self.assertEqual(responsive_density_for_width(1440), "roomy")
        self.assertEqual(responsive_content_width(500), 476)
        self.assertEqual(responsive_content_width(800), 764)
        self.assertEqual(responsive_content_width(1440), 1240)

    def test_context_manager_stacks_and_unstacks_panels(self):
        class Owner(QWidget):
            def __init__(self):
                super().__init__()
                self.conf = {"context_items": []}

            def set_context_item_enabled(self, *_args):
                pass

        dialog = ContextManagerDialog(Owner())
        dialog._reflow_panels(560)
        self.assertEqual(
            dialog.body_layout.getItemPosition(
                dialog.body_layout.indexOf(dialog.reference_panel)
            )[:2],
            (1, 0),
        )
        dialog._reflow_panels(900)
        self.assertEqual(
            dialog.body_layout.getItemPosition(
                dialog.body_layout.indexOf(dialog.reference_panel)
            )[:2],
            (0, 1),
        )

    def test_context_add_actions_stack_on_narrow_dialog(self):
        class Owner(QWidget):
            pass

        dialog = ContextAddDialog(Owner())
        dialog._reflow_choices(520)
        project_pos = dialog.choices_layout.getItemPosition(
            dialog.choices_layout.indexOf(dialog.project_action)
        )
        upload_pos = dialog.choices_layout.getItemPosition(
            dialog.choices_layout.indexOf(dialog.upload_action)
        )
        self.assertEqual(project_pos[:2], (0, 0))
        self.assertEqual(upload_pos[:2], (2, 0))

    def test_model_onboarding_uses_tabs_when_columns_are_too_narrow(self):
        dialog = ModelOnboardingDialog(
            None, [], OPENROUTER_TRANSCRIPTION_CHOICES[0][2],
            OPENROUTER_CLEANUP_CHOICES[0][2],
        )
        dialog._reflow_model_columns(640)
        self.assertIs(dialog.model_switcher.currentWidget(), dialog.model_tabs)
        self.assertEqual(dialog.model_tabs.count(), 2)
        dialog._reflow_model_columns(980)
        self.assertIs(dialog.model_switcher.currentWidget(), dialog.model_wide_page)
        self.assertEqual(dialog.model_tabs.count(), 0)


if __name__ == "__main__":
    unittest.main()
