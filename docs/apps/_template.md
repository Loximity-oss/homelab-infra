# <App> — design note (Configuration Engineer)

| | |
|---|---|
| Guest / zone | `hm1-<zone>-<role><nn>` |
| Service name | `<service>.lox-internal.dev` (services.yaml) |
| Ports | |
| Data | paths on the guest — **not backed up** (open item A-1) |
| Secrets | names only: `<zone>/secret/apps/<app>` keys … |
| TLS | ACME, OpenBao PKI role `<zone>` |
| Upgrade | |
| Healthcheck | |
