# k3s runbook - moved to homelab-apps

See homelab-apps `catalog/k3s/` (http://192.168.70.20:3000/homelab/homelab-apps) and the apps-runner runbook in the SRE skill `sre-runbooks`. Restore k3s control nodes only as a set, after `k3s etcd-snapshot save`; never one alone. L4 failures open `app-verify: k3s/<host>` in homelab-apps.
