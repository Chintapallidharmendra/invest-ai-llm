#!/bin/sh
# Verify zero egress from inside the api and worker containers (Story 1.3, ADR-034).
#
#   sudo deploy/host/verify-egress.sh              # check the running stack
#   sudo deploy/host/verify-egress.sh --self-test  # prove the check detects egress
#
# PASS only if, from each of api and worker:
#   - a TCP connection to a public IP fails,
#   - a public DNS name does not resolve or cannot be connected to,
#   - postgres:5432 and valkey:6379 are reachable;
# and caddy (on the non-internal `edge` network) cannot reach a public IP.
#
# --self-test runs the same probe in a throwaway container on Docker's default bridge
# network (which has an internet route on a laptop). There the egress checks must FAIL;
# the internal-service checks are skipped. On the UAT VM, where outbound traffic is
# blocked at the Azure network level, the self-test reports no egress either; that is
# expected.
#
# Environment: COMPOSE_FILE (default deploy/compose.yml next to this script),
# COMPOSE_ENV_FILE (optional --env-file), PUBLIC_IP (default 1.1.1.1),
# PUBLIC_NAME (default example.com).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
COMPOSE_FILE="${COMPOSE_FILE:-$HERE/../compose.yml}"
PUBLIC_IP="${PUBLIC_IP:-1.1.1.1}"
PUBLIC_NAME="${PUBLIC_NAME:-example.com}"

# The probe runs with the container's Python (no curl/nc in the images).
PROBE=$(cat <<'PY'
import socket, sys

public_ip, public_name, check_internal = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
TIMEOUT = 4.0
failures = 0

def connect(host, port):
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT):
            return True, "connected"
    except OSError as exc:
        return False, type(exc).__name__

def report(ok, label, detail):
    global failures
    failures += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} ({detail})")

for port in (443, 53):
    reached, detail = connect(public_ip, port)
    report(not reached, f"no TCP to public IP {public_ip}:{port}", detail)

try:
    addr = socket.getaddrinfo(public_name, 443, proto=socket.IPPROTO_TCP)[0][4][0]
except OSError as exc:
    report(True, f"no route to public name {public_name}", f"DNS {type(exc).__name__}")
else:
    reached, detail = connect(addr, 443)
    report(not reached, f"no route to public name {public_name}", f"resolved; {detail}")

if check_internal:
    for host, port in (("postgres", 5432), ("valkey", 6379)):
        reached, detail = connect(host, port)
        report(reached, f"{host}:{port} reachable", detail)

sys.exit(1 if failures else 0)
PY
)

compose() {
    if [ -n "${COMPOSE_ENV_FILE:-}" ]; then
        docker compose -f "$COMPOSE_FILE" --env-file "$COMPOSE_ENV_FILE" "$@"
    else
        docker compose -f "$COMPOSE_FILE" "$@"
    fi
}

if [ "${1:-}" = "--self-test" ]; then
    image="${SELF_TEST_IMAGE:-invest-ai-llm/backend:${APP_VERSION:-dev}}"
    echo "self-test: probe on the default bridge network ($image)"
    if docker run --rm --network bridge --entrypoint python "$image" \
        -c "$PROBE" "$PUBLIC_IP" "$PUBLIC_NAME" 0; then
        echo "self-test: no egress on the default bridge either (expected on the UAT VM)."
    else
        echo "self-test: egress detected on the default bridge, so the probe works."
    fi
    exit 0
fi

status=0
for service in api worker; do
    echo "$service:"
    if ! compose exec -T "$service" python -c "$PROBE" "$PUBLIC_IP" "$PUBLIC_NAME" 1; then
        status=1
    fi
done

# Caddy is the only container on a non-internal network (edge, for the published
# port). Its egress is blocked by disabled NAT on `edge` plus the host/Azure firewall.
echo "caddy:"
if compose exec -T caddy wget -q -T 4 -O /dev/null "http://$PUBLIC_IP/" 2>/dev/null; then
    echo "  [FAIL] no TCP to public IP $PUBLIC_IP:80 (connected)"
    status=1
else
    echo "  [PASS] no TCP to public IP $PUBLIC_IP:80"
fi

if [ "$status" -eq 0 ]; then
    echo "verify-egress: PASS"
else
    echo "verify-egress: FAIL"
fi
exit "$status"
