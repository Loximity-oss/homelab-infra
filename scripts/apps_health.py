#!/usr/bin/env python3
"""Patch health from the homelab-apps catalogue (proposal 3-patching 3.2, 1.3; migration step 9).

  apps_health.py build APPS_DIR HOSTS OUT
      OUT = extra vars for patch.yml:
        {"patch_apps_health": {host: [{app, zone, services, timeout_s, script}]}, "patch_apps_health_sha": "<apps sha>"}
      for every host in HOSTS (csv) that is in a deployments/<zone>/<app>.yaml whose catalogue entry has `health`.
      `script` runs with /bin/bash on the guest: each service must be active, then the command under `timeout`.
      Prints the covered hosts (csv). Any unreadable/malformed deployment or entry -> exit 1: ci-patch.sh then passes
      no file and patch_apply runs no app checks (fallback: L3 baseline only, with a warning in the run).
  apps_health.py selftest
      offline unit test of compose() and the validation.

Read-only: never writes to the apps checkout. `example` entries are skipped."""
import json, pathlib, re, shlex, subprocess, sys

HOST = re.compile(r"^hm1-[a-z]+-[a-z][a-z0-9]*[0-9]{2}$")
APP = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
UNIT = re.compile(r"^[A-Za-z0-9@._:-]{1,120}$")
MAX_CMD = 8192


class Bad(Exception):
    pass


def health_of(entry, where):
    h = entry.get("health")
    if h is None:
        return None
    if not isinstance(h, dict):
        raise Bad(f"{where}: health is not a mapping")
    services = h.get("services") or []
    if not isinstance(services, list) or any(not isinstance(s, str) or not UNIT.match(s) for s in services):
        raise Bad(f"{where}: health.services must be a list of unit names")
    cmd = h.get("command")
    if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > MAX_CMD or "\x00" in cmd:
        raise Bad(f"{where}: health.command must be a non-empty string (<= {MAX_CMD} chars)")
    t = h.get("timeout_s")
    if not isinstance(t, int) or isinstance(t, bool) or not 1 <= t <= 900:
        raise Bad(f"{where}: health.timeout_s must be an integer 1-900")
    return {"services": services, "command": cmd, "timeout_s": t}


def compose(h):
    """One bash line: services active, then the command with a hard timeout (exit 124 on timeout)."""
    parts = [f"systemctl is-active --quiet {shlex.quote(s)}" for s in h["services"]]
    parts.append(f"timeout {int(h['timeout_s'])} bash -c {shlex.quote(h['command'])}")
    return " && ".join(parts)


def load(path):
    import yaml
    if path.is_symlink():
        raise Bad(f"{path}: symlink refused")
    d = yaml.safe_load(path.read_text())
    if not isinstance(d, dict):
        raise Bad(f"{path}: not a mapping")
    return d


def collect(root, want):
    root = pathlib.Path(root)
    if not (root / ".git").exists() and not (root / "README.md").exists():
        raise Bad(f"{root}: not a homelab-apps checkout")
    out, entries = {}, {}
    for p in sorted(root.glob("deployments/*/*.yaml")):
        d = load(p)
        app, zone, hosts = d.get("app"), p.parent.name, d.get("hosts") or []
        if not isinstance(app, str) or not APP.match(app):
            raise Bad(f"{p}: bad app {app!r}")
        if not isinstance(hosts, list) or any(not isinstance(h, str) or not HOST.match(h) for h in hosts):
            raise Bad(f"{p}: bad hosts")
        if app not in entries:
            e = load(root / "catalog" / app / "app.yaml")
            if (e.get("catalog") or {}).get("status") == "example":
                entries[app] = None
            else:
                entries[app] = health_of(e, f"catalog/{app}/app.yaml")
        h = entries[app]
        if h is None:
            continue
        for host in hosts:
            if host in want:
                out.setdefault(host, []).append({"app": app, "zone": zone, "services": h["services"],
                                                 "timeout_s": h["timeout_s"], "script": compose(h)})
    return out


def build(apps_dir, hosts, out):
    want = {h for h in hosts.split(",") if h}
    try:
        res = collect(apps_dir, want)
    except (Bad, OSError, ValueError) as e:
        sys.exit(str(e))
    except Exception as e:  # yaml errors etc.
        sys.exit(f"{type(e).__name__}: {e}")
    sha = subprocess.run(["git", "-C", apps_dir, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    with open(out, "w") as f:
        json.dump({"patch_apps_health": res, "patch_apps_health_sha": sha or "unknown"}, f, indent=1, sort_keys=True)
    print(",".join(sorted(res)))


def selftest():
    bad = []
    s = compose({"services": ["AdGuardHome"], "command": "dig @127.0.0.1 x +short | grep -qx '192.0.2.53'", "timeout_s": 20})
    want = ("systemctl is-active --quiet AdGuardHome && timeout 20 bash -c "
            "'dig @127.0.0.1 x +short | grep -qx '\"'\"'192.0.2.53'\"'\"''")
    if s != want:
        bad.append(f"compose: {s!r}")
    if compose({"services": [], "command": "true", "timeout_s": 5}) != "timeout 5 bash -c true":
        bad.append("compose without services")
    for h, why in [({"services": "x", "command": "true", "timeout_s": 5}, "services not a list"),
                   ({"services": ["a b"], "command": "true", "timeout_s": 5}, "unit with a space"),
                   ({"services": ["x;reboot"], "command": "true", "timeout_s": 5}, "unit with ;"),
                   ({"services": [], "command": "", "timeout_s": 5}, "empty command"),
                   ({"services": [], "command": "true", "timeout_s": 0}, "timeout 0"),
                   ({"services": [], "command": "true", "timeout_s": True}, "timeout bool"),
                   ({"services": [], "command": "true", "timeout_s": "20"}, "timeout string")]:
        try:
            health_of({"health": h}, "t")
            bad.append(f"accepted: {why}")
        except Bad:
            pass
    if health_of({}, "t") is not None:
        bad.append("no health must be None")
    print("\n".join(bad) or "selftest ok")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if cmd == "build" and len(args) == 3:
        build(*args)
    elif cmd == "selftest":
        selftest()
    else:
        sys.exit(__doc__)
