"""Add the H3C prompt-sound case to patch 0006 (upstream 2.4.1 test_h3c.py).

Usage: extend_0006.py <upstream-worktree>
"""
import sys
from pathlib import Path

path = Path(sys.argv[1], "tests/pybambu/test_h3c.py")
text = path.read_text(encoding="utf-8")

setup_old = (
    "        self.client = MagicMock()\n"
    "        self.client._device_type = Printers.H3C\n"
)
setup_new = (
    "        self.client = MagicMock()\n"
    "        # Stock client: no signing credentials configured.\n"
    "        self.client.mqtt_signing_enabled = False\n"
    "        self.client._device_type = Printers.H3C\n"
)
anchor = "    def test_heatbed_light_tracks_commands_despite_error_response(self):\n"
added = (
    "    def test_prompt_sound_stays_available_when_commands_can_be_signed(self):\n"
    "        # With signing configured the printer accepts the command, so the\n"
    "        # prompt sound stays usable although signatures are required.\n"
    "        self.client.mqtt_signing_enabled = True\n"
    "        self.load()\n"
    "        self.device.print_fun.print_update({\"fun\": \"20000000\"})\n"
    "        self.assertTrue(self.device.print_fun.mqtt_signature_required)\n"
    "        self.assertTrue(self.device.supports_feature(Features.PROMPT_SOUND))\n"
    "\n"
)
assert text.count(setup_old) == 1 and text.count(anchor) == 1
text = text.replace(setup_old, setup_new).replace(anchor, added + anchor)
path.write_text(text, encoding="utf-8")
print("extended", path)
