# tasks: REQ-runner-net-tools-1777202552

## Stage: contract / spec

- [x] author `specs/runner-net-tools/spec.md` with delta `## ADDED Requirements`
- [x] write 4 scenarios `RUNNER-NET-S{1..4}` covering `ip`, `ss`, `netstat`,
      `ifconfig`

## Stage: implementation

- [x] `runner/Dockerfile`: extend §1 apt-get install list with
      `iproute2 net-tools`
- [x] `runner/go.Dockerfile`: extend §1 apt-get install list with
      `iproute2 net-tools`

## Stage: PR

- [x] git push `feat/REQ-runner-net-tools-1777202552`
- [x] gh pr create
