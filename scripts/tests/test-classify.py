#!/usr/bin/env python3
"""Unit tests for policy/classify.py protected-bridge detection (LXC network_interface and VM network_device).
Offline: runs classify.py as a subprocess on synthetic plan JSON. Run: python3 scripts/tests/test-classify.py"""
import json, pathlib, subprocess, sys, tempfile, unittest

SRC = pathlib.Path(__file__).resolve().parents[2] / "policy" / "classify.py"
PROTECTED = "vmids:\n  300: x\ntemplates: [9000, 9199]\nbridges: [vmbr0]\nsubnets: [192.168.67.0/28]\n"


def rc(rtype, actions, before=None, after=None, name="g"):
    return {"address": f"module.guests.{rtype}.{name}", "type": rtype,
            "change": {"actions": actions, "before": before, "after": after}}


def classify(changes):
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "changed.json").write_text(json.dumps({"files": [], "stacks": ["terraform/stacks/hm1/stg"]}))
        (d / "protected.yaml").write_text(PROTECTED)
        plans = d / "plans"; plans.mkdir()
        (plans / "hm1-stg.plan.json").write_text(json.dumps({"resource_changes": changes}))
        out = subprocess.run([sys.executable, "-I", str(SRC), "--changed", str(d / "changed.json"), "--plans", str(plans),
                              "--protected", str(d / "protected.yaml")], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)


VM = "proxmox_virtual_environment_vm"
CT = "proxmox_virtual_environment_container"


class ProtectedBridge(unittest.TestCase):
    def test_vm_create_on_vmbr0_is_c(self):
        r = classify([rc(VM, ["create"], after={"vm_id": 210, "network_device": [{"bridge": "vmbr0"}]})])
        self.assertEqual(r["class"], "C")
        self.assertTrue(any("protected bridge vmbr0 (network_device)" in x for x in r["reasons"]))

    def test_vm_update_moving_off_vmbr0_is_c(self):
        r = classify([rc(VM, ["update"], before={"vm_id": 210, "network_device": [{"bridge": "vmbr0"}]},
                         after={"vm_id": 210, "network_device": [{"bridge": "vmbr1"}]})])
        self.assertEqual(r["class"], "C")

    def test_lxc_on_vmbr0_still_c(self):
        r = classify([rc(CT, ["create"], after={"vm_id": 211, "network_interface": [{"bridge": "vmbr0"}]})])
        self.assertEqual(r["class"], "C")
        self.assertTrue(any("(network_interface)" in x for x in r["reasons"]))

    def test_vm_on_guest_bridge_is_a(self):
        r = classify([rc(VM, ["create"], after={"vm_id": 210, "network_device": [{"bridge": "vmbr1", "vlan_id": 40}]})])
        self.assertEqual(r["class"], "A")

    def test_malformed_nic_entries_ignored(self):
        r = classify([rc(VM, ["create"], after={"vm_id": 210, "network_device": [None, "vmbr1"]})])
        self.assertEqual(r["class"], "A")


if __name__ == "__main__":
    unittest.main()
