# AdGuard Home runbook - moved to homelab-apps

See homelab-apps `catalog/adguard/` (http://192.168.70.20:3000/homelab/homelab-apps) and the apps-runner runbook in the SRE skill `sre-runbooks`. Failures of the L4 check open an `app-verify: adguard/<host>` issue in homelab-apps. Rollback uses the `pre-deploy_*` snapshot (see the runner runbook).
