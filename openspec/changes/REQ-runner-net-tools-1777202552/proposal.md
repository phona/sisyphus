# fix(runner): add iproute2 + net-tools

## Why

Both runner images (`runner/Dockerfile` Flutter flavor and `runner/go.Dockerfile`
Go flavor) are missing the basic IPv4 networking userland: no `ip`, no `ss`, no
`netstat`, no `ifconfig`, no `route`. These tools are routinely needed during
`accept-env-up` / acceptance scenarios to:

- inspect what `docker compose up` actually wired (`ip addr`, `ip route`,
  `ip -br link`),
- check whether a service port is listening before probing it
  (`ss -ltn` / `netstat -ltn`),
- diagnose DNS / routing issues from inside the per-REQ Pod when an integration
  scenario can't reach a sibling container (`ip route get <ip>`).

Currently every `accept-env-*` author who wants to debug from the runner Pod
hits `bash: ip: command not found` and has to either install on the fly (slow,
non-deterministic, lost on Pod restart) or shell out to `docker exec` round-trips
that make Makefile probes brittle. Both packages are tiny (`iproute2` ~1.5 MB,
`net-tools` <1 MB compressed) and stable Debian/Ubuntu apt names — there is no
ongoing maintenance cost to baking them in.

## What Changes

- **`runner/Dockerfile`** (Flutter flavor, Ubuntu base) — section §1 `apt-get
  install` list gains `iproute2 net-tools`.
- **`runner/go.Dockerfile`** (Go flavor, Debian bookworm base) — section §1
  `apt-get install` list gains `iproute2 net-tools`.

That's the whole change. Nothing else: same `apt-get` invocation, same layer,
same `--no-install-recommends`, same `rm -rf /var/lib/apt/lists/*`.

## Impact

- **Affected specs**: new capability `runner-net-tools` (purely additive, names
  the four binaries the runner image guarantees).
- **Affected code**: `runner/Dockerfile`, `runner/go.Dockerfile`. No
  orchestrator, helm chart, scripts, or Pod spec changes.
- **Image size**: <3 MB added per flavor (tiny vs. existing ~1 GB Go and ~5 GB
  Flutter images).
- **Risk**: trivial. Both packages are part of every Debian/Ubuntu base; apt
  resolution is deterministic on the pinned base images already in use. No
  runtime behavior of the runner changes — these are user-invoked diagnostic
  binaries only, not wired into entrypoint or any sisyphus checker.
- **Out of scope**: `tcpdump`, `traceroute`, `nmap`, `dig`, `bind9-host`,
  `mtr`. Each of those is bigger and only sometimes useful — add them per-REQ
  if a specific scenario actually needs them. This REQ stays narrow on the
  daily-use IPv4 toolkit.
