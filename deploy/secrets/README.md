# deploy/secrets

Host-only secret files mounted into containers. **Everything here except this README is
git-ignored.** On the UAT VM the real files live outside the repo:

| File | Host path | Used by |
|---|---|---|
| `db_superuser_password` | `/etc/foundry/secrets/` | postgres (init only; superuser is socket-only afterwards) |
| `db_migrator_password` | `/etc/foundry/secrets/` | postgres init, migrate (role `app_migrator`) |
| `db_app_rw_password` | `/etc/foundry/secrets/` | postgres init, api, worker (role `app_rw`) |
| `cache_salt_secret` | `/etc/foundry/secrets/` | api, worker (Story 1.5) |
| `audit_hmac_key` | `/etc/foundry/secrets/` | api, worker (Story 7.1) |
| `backup_key` | `/etc/foundry/secrets/` | worker (Story 7.5) |
| `kek` | `/etc/foundry/kek` | api, worker (Story 8.1). Excluded from backups. |

- Host files: owner `root:root`, mode `0400`, directory `0700`. Compose copies each file
  into `/run/secrets/<name>` with the container user's uid and mode `0400`.
- Generate DB passwords as hex (`openssl rand -hex 32`): the entrypoint rejects `:` and `\`.
- See `deploy/runbook.md` for creation and rotation.
