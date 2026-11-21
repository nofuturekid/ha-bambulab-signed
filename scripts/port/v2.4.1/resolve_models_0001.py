"""Resolve patch 0001's two conflicts in pybambu/models.py on v2.4.1.

PROMPT_SOUND: upstream's printer set (adds h3_printers) with 0001's
mqtt_control_blocked. PrintFun events: upstream #2275 fires the
enabled/disabled pair both ways; 0001 suppresses it while we can sign.
Same resolution as ClusterM 07c0039.
"""
import re
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
blocks = re.findall(r"<<<<<<< .*?\n>>>>>>> [^\n]*\n", text, flags=re.S)
assert len(blocks) == 2, len(blocks)

prompt = (
    "            if model in (a1_printers | a2_printers | h2_printers | h3_printers | p2_printers | x2_printers):\n"
    "                return not self.print_fun.mqtt_control_blocked\n"
)
events = (
    "        if self._encryption_enabled and not self._client.mqtt_signing_enabled and not self._fired_encryption_enabled_event:\n"
    "            self._fired_encryption_enabled_event = True\n"
    "            self._client.callback(\"event_printer_mqtt_encryption_enabled\")\n"
    "        elif not self._encryption_enabled and not self._client.mqtt_signing_enabled and self._fired_encryption_enabled_event:\n"
    "            # Signing is no longer required (e.g. developer mode was turned on). The\n"
    "            # control entities were skipped at setup, so let the coordinator reload.\n"
    "            self._fired_encryption_enabled_event = False\n"
    "            self._client.callback(\"event_printer_mqtt_encryption_disabled\")\n"
)
assert "PROMPT" not in blocks[0] and "h3_printers" in blocks[0]
assert "_fired_encryption_enabled_event" in blocks[1]
text = text.replace(blocks[0], prompt, 1).replace(blocks[1], events, 1)
open(path, "w", encoding="utf-8").write(text)
print("resolved", path)
