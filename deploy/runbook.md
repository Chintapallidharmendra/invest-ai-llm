# invest-ai-llm deployment runbook (v0)

Scope: the Azure UAT VM `nbfcuatyolomlapp01` (36 vCPU, 432 GiB RAM, NVIDIA A10-24Q). The
host has no outbound internet route, and only inbound ports 8000–8050 are open. The app
serves HTTPS on **8043**. **UAT holds synthetic test documents only.**

Architecture references: ADR-034 (zero-egress deployment), ADR-028 (KEK, disk
encryption), ADR-035 (backups, WAL), ADR-021 (secrets).

All commands run as root (`sudo -i`) from the repository checkout, e.g. `/opt/invest-ai-llm`.

---

## 1. Host preparation (once)

### 1.1 Disks: `/data` only, never `/mnt`

| Mount | Size | Use |
|---|---|---|
| `/` | 62 GB | OS, `/etc/foundry` (secrets, KEK) |
| `/data` | 251 GB, persistent | Docker data-root, Postgres, Caddy CA, backups, models |
| `/mnt` | 1.4 TB, **ephemeral Azure temp disk** | **Never used.** Wiped on redeploy or resize; must hold no content. |

```sh
findmnt /data            # must be the persistent managed disk
mkdir -p /data/docker /data/postgres /data/caddy /data/backups
chmod 0700 /data/postgres /data/backups
```

### 1.2 Docker data-root on `/data`

Requirements: Docker Engine with Compose **≥ 2.20** (for `include:`). Development used
Compose v2.39.4 / Engine 28.4. Compose must copy file secrets with per-service
uid/mode; step 5.2 checks this.

```sh
install -m 0644 deploy/host/daemon.json /etc/docker/daemon.json
systemctl restart docker
docker compose version             # record it here: ______________
```

**Check (must fail loudly if wrong):**

```sh
root=$(docker info --format '{{.DockerRootDir}}')
[ "$root" = "/data/docker" ] || { echo "FATAL: Docker data-root is $root, expected /data/docker"; exit 1; }
case "$root" in /mnt*) echo "FATAL: Docker data-root on ephemeral /mnt"; exit 1 ;; esac
```

If Docker ran with the default root (`/var/lib/docker` on the 62 GB OS disk) before,
stop Docker, move or delete the old data, then restart with the new `daemon.json`.

### 1.3 Network: no egress

1. **Azure NSG:** confirm outbound internet is denied (Azure portal or IT). Inbound:
   only 8000–8050 (plus SSH from the admin network).
2. **Optional host default-deny:** review and adapt `deploy/host/nftables.conf.example`
   (interface name, Azure platform IPs, backup target), then load it from a second SSH
   session:
   ```sh
   install -m 0600 deploy/host/nftables.conf.example /etc/nftables.d/invest-ai-egress.nft
   nft -f /etc/nftables.d/invest-ai-egress.nft
   ```
3. Nothing else on the host may bind ports 8000–8050:
   ```sh
   ss -ltnp | awk '$4 ~ /:80[0-4][0-9]$|:8050$/'
   ```
   The only listener in that range after start-up must be docker-proxy or DNAT for 8043.

---

## 2. Secrets and KEK

Secrets are Compose **file secrets**. They never go in a compose file, an image or
`.env`. Compose copies each one into `/run/secrets/<name>` inside the container, owned by
the container user with mode `0400`.

| File | Purpose |
|---|---|
| `/etc/foundry/secrets/db_superuser_password` | Postgres superuser (init only; socket access afterwards) |
| `/etc/foundry/secrets/db_migrator_password` | role `app_migrator` (DDL) |
| `/etc/foundry/secrets/db_app_rw_password` | role `app_rw` (DML, subject to RLS) |
| `/etc/foundry/secrets/cache_salt_secret` | per-user vLLM cache salt (Story 1.5) |
| `/etc/foundry/secrets/audit_hmac_key` | audit chain HMAC (Story 7.1) |
| `/etc/foundry/secrets/backup_key` | backup encryption (Story 7.5) |
| `/etc/foundry/kek` | key-encryption key (Story 8.1). **Never in any backup.** |

Create them once (hex only: the entrypoint rejects `:` and `\` in DB passwords):

```sh
install -d -m 0700 -o root -g root /etc/foundry /etc/foundry/secrets
umask 077
for name in db_superuser_password db_migrator_password db_app_rw_password \
            cache_salt_secret audit_hmac_key backup_key; do
    [ -f "/etc/foundry/secrets/$name" ] || openssl rand -hex 32 > "/etc/foundry/secrets/$name"
done
[ -f /etc/foundry/kek ] || openssl rand -hex 32 > /etc/foundry/kek
chown root:root /etc/foundry/kek /etc/foundry/secrets/*
chmod 0400 /etc/foundry/kek /etc/foundry/secrets/*
ls -l /etc/foundry /etc/foundry/secrets      # all -r-------- root root
```

- **KEK custody:** the KEK lives on the OS disk only. Exclude `/etc/foundry` from every
  backup; escrow follows the procedure in architecture AQ-6 (open). Losing the KEK makes
  all encrypted content unrecoverable.
- **DB passwords** are applied when Postgres first initialises an empty
  `/data/postgres`. Changing a file later does not change the role password; rotate with
  `ALTER ROLE ... PASSWORD` (step 6) and then update the file.

---

## 3. Images: offline load

The VM cannot pull images. Build and save on a connected build machine, copy over
approved media or an internal registry, verify, then load.

**Build machine:**

```sh
export APP_VERSION=2026.10.07-1            # example tag
docker compose -f deploy/compose.yml build
docker pull pgvector/pgvector:pg18 && docker pull valkey/valkey:8
docker save -o invest-ai-llm-$APP_VERSION.tar \
    invest-ai-llm/backend:$APP_VERSION invest-ai-llm/caddy:$APP_VERSION \
    pgvector/pgvector:pg18 valkey/valkey:8
sha256sum invest-ai-llm-$APP_VERSION.tar > invest-ai-llm-$APP_VERSION.tar.sha256
```

Send the `.sha256` through a separate channel from the tarball (e.g. a ticket).

**VM:**

```sh
sha256sum -c invest-ai-llm-$APP_VERSION.tar.sha256    # must print: OK
docker load -i invest-ai-llm-$APP_VERSION.tar
docker image ls 'invest-ai-llm/*'
```

Never load an image whose checksum does not match. Every service uses
`pull_policy: never`, so a missing image fails fast instead of attempting a pull.

---

## 4. Configuration

```sh
cp deploy/env.example deploy/.env
vi deploy/.env     # APP_VERSION, APP_SITE_ADDRESSES (hostname and/or IP on :8043), APP_TLS
```

`deploy/.env` holds **non-secret** settings only and is git-ignored.

**TLS:**
- `APP_TLS=internal`: Caddy's local CA, kept in `/data/caddy`. Distribute the root
  certificate to users' browsers:
  `docker compose -f deploy/compose.yml cp caddy:/data/caddy/pki/authorities/local/root.crt ./invest-ai-root.crt`
- **Provided certificate:** put `cert.pem` and `key.pem` in `/etc/foundry/tls` (key
  `0400 root`) and set `APP_TLS=/etc/caddy/tls/cert.pem /etc/caddy/tls/key.pem`.

---

## 5. Start, verify, stop

### 5.1 Start

```sh
docker compose -f deploy/compose.yml up -d
docker compose -f deploy/compose.yml ps -a
```

Expected: `postgres`, `valkey`, `api`, `worker` and `caddy` are healthy; `migrate`
shows `Exited (0)`. If `migrate` fails, `api`, `worker` and `caddy` stay `Created`:
check `docker compose -f deploy/compose.yml logs migrate`.

The included files `compose.inference.yml` (Story 1.4), `compose.parser.yml` (9.2) and
`compose.observability.yml` (7.10) start with no services. Jaeger starts only with
`--profile observability`.

### 5.2 Verify

```sh
# Only caddy publishes a port, and only 8043.
docker compose -f deploy/compose.yml ps --format '{{.Service}} {{.Publishers}}'

# HTTPS, headers and body limit (from the VM or a user's machine).
curl -sk -D - -o /dev/null https://<host>:8043/healthz    # 200 + HSTS, CSP, nosniff, no-referrer
curl -sk -o /dev/null -w '%{http_code}\n' https://<host>:8043/readyz
head -c 62914560 /dev/zero | curl -sk -o /dev/null -w '%{http_code}\n' \
    -X POST --data-binary @- https://<host>:8043/api/v1/upload        # 413

# Secrets are copied with the container user's uid and mode 0400, and none are in env.
docker compose -f deploy/compose.yml exec api ls -ln /run/secrets
docker inspect invest-ai-llm-api-1 --format '{{json .Config.Env}}'   # no secret values

# DB roles: app_rw cannot bypass RLS and cannot create tables.
docker compose -f deploy/compose.yml exec -u postgres postgres psql -d invest_ai -c \
    "select rolname, rolbypassrls, rolsuper from pg_roles where rolname like 'app_%'"

# Zero egress from api and worker (and caddy); internal services reachable.
deploy/host/verify-egress.sh                 # must print: verify-egress: PASS
```

### 5.3 Stop

```sh
docker compose -f deploy/compose.yml down       # data in /data/postgres is kept
```

Never use `down -v` on UAT unless the database is meant to be destroyed.

---

## 6. Operations notes

- **Postgres superuser:** socket-only. Connect with
  `docker compose -f deploy/compose.yml exec -u postgres postgres psql -d invest_ai`.
- **Rotate a DB password:**
  `ALTER ROLE app_rw PASSWORD '<new hex>';` then write the same value to
  `/etc/foundry/secrets/db_app_rw_password` and `docker compose up -d --force-recreate api worker`.
- **WAL:** `wal_keep_size = 0`, `archive_mode = off`, `max_wal_size = 1GB`. This bounds
  how long deleted ciphertext lingers in WAL (ADR-035).
- **Logs are content-free:** Postgres doesn't log statements, parameters or DETAIL
  lines. Caddy's access log drops URIs and headers. The API logs allow-listed fields
  only. Container logs rotate (local driver, 5 × 20 MB).
- **Backups (interim):** local only, under `/data/backups`, 2-day retention
  (Story 7.5). `/data/backups` and `/etc/foundry` are never copied into the repo. An
  off-host target is required before real deal data (open question with IT).
- **Valkey** keeps no persistence (`--save ""`, no AOF) and no auth yet. It is
  reachable only on the internal `app` network.
