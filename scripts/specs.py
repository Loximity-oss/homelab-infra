"""Load guest specs: terraform/stacks/<site>/<zone>/guests/<hostname>.yaml"""
import pathlib
import yaml

import os
ROOT = pathlib.Path(os.environ.get("INFRA_ROOT") or pathlib.Path(__file__).resolve().parents[1])


def no_os(s):
    """VM with a blank disk and no OS since day 0 (os: none + tests: l1-only; lint/naming.py pairs them, Faris
    2026-10-31): never configured by Ansible (not in the inventory), tested at L1 only (verify.py)."""
    return s.get("type") == "vm" and s.get("os") == "none" and s.get("tests") == "l1-only"


def load_specs(root=ROOT):
    out = []
    for p in sorted(pathlib.Path(root).glob("terraform/stacks/*/*/guests/*.yaml")):
        s = yaml.safe_load(p.read_text()) or {}
        s["_zone"] = p.parent.parent.name
        s["_site"] = p.parent.parent.parent.name
        s["_path"] = str(p.relative_to(root))
        out.append(s)
    return out
