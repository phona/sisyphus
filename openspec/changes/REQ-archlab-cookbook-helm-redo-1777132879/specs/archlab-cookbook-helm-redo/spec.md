# archlab-cookbook-helm-redo Specification

## MODIFIED Requirements

### Requirement: cookbook MUST 改用 helm chart 路径描述 accept-env-up 五步 recipe

The sisyphus repo's cookbook document at `docs/cookbook/ttpos-arch-lab-accept-env.md`
SHALL be rewritten so that the five-step `accept-env-up` recipe uses the helm chart
path: (1) `helm upgrade --install` for the backend chart with `--wait --timeout 5m`;
(2) `helm upgrade --install` for the emulator chart followed by `kubectl exec`-based
`boot_completed` polling; (3) `flutter build apk --release` with
`--dart-define=API_BASE_URL` set to the cluster-internal DNS form
`http://lab.<namespace>.svc.cluster.local:8080`; (4) ADB connect to the emulator
Service ClusterIP and `adb install -r`; (5) emit endpoint JSON on the final stdout
line. The recipe MUST NOT reference `docker compose up` or docker host port allocation
as the primary mechanism for the backend or emulator stacks. The cookbook MUST retain
a §0 TL;DR section summarising these five steps so that readers can confirm the recipe
shape before reading the detailed sections.

#### Scenario: HELMREDO-S1 TL;DR section names helm upgrade for backend and emulator

- **GIVEN** the file `docs/cookbook/ttpos-arch-lab-accept-env.md`
- **WHEN** the `## 0` section body is read
- **THEN** the body contains the literal string `helm upgrade --install` at least
  once AND the body references `boot_completed` or `kubectl exec` as the emulator
  readiness mechanism, AND the body does **not** contain the literal string
  `docker compose up` as a primary recipe step

### Requirement: cookbook §1 repo layout MUST 列 charts/ 目录而非 docker-compose 文件

The `## 1` repo layout section SHALL show a directory tree that includes a
`charts/` directory containing at least two subdirectories: one for the backend
chart (named `accept-lab` or equivalent) and one for the emulator chart. The
section MUST NOT list `docker-compose.accept.yml` as a required top-level file
in the new layout. The `emulator/boot-wait-k8s.sh` helper script MUST appear in
the layout tree so that implementers know to create it.

#### Scenario: HELMREDO-S2 §1 layout tree lists charts/accept-lab and charts/emulator

- **GIVEN** the cookbook `## 1` section
- **WHEN** the section body is scanned for directory tree entries
- **THEN** the body contains the literal strings `charts/accept-lab` (or
  `accept-lab/`) AND `charts/emulator` (or `emulator/` under `charts/`),
  AND the body contains `boot-wait-k8s.sh`

### Requirement: cookbook MUST 提供 emulator pod spec 的软件渲染声明（K8s privileged + -no-accel）

The emulator helm chart section SHALL include a Kubernetes pod spec fragment that
sets `securityContext.privileged: true` AND sets the `EMULATOR_ARGS` environment
variable to a value containing both `-no-accel` and `-gpu swiftshader_indirect`,
so that the emulator runs with software rendering without requiring `/dev/kvm`.
The section MUST also explain why no `readinessProbe` is declared on the emulator
container (boot time ~3 min exceeds typical probe tolerance) and that boot
detection is delegated to `boot-wait-k8s.sh`. Without this explanation, cookbook
readers would add a readinessProbe and trigger restart loops on slow emulator boot.

#### Scenario: HELMREDO-S3 emulator chart section has privileged + -no-accel + swiftshader_indirect

- **GIVEN** the emulator helm chart section of the cookbook
- **WHEN** the section body is scanned
- **THEN** the body contains the literal token `privileged: true` AND the literal
  tokens `-no-accel` AND `swiftshader_indirect`, AND the body contains text
  explaining the absence of `readinessProbe` or delegating boot detection to
  a separate step

### Requirement: cookbook §5 endpoint JSON MUST 使用 cluster DNS 而非 localhost port

The endpoint JSON section SHALL document the `endpoint` key as a cluster-internal
DNS URL of the form `http://lab.<namespace>.svc.cluster.local:8080` and the `adb`
key as `<ClusterIP>:5555` where ClusterIP is obtained via
`kubectl get svc emulator-adb -o jsonpath`. The section MUST explain that both
addresses are reachable from the runner pod because the runner pod runs inside
the same K3s cluster. The section MUST preserve the backward-compatible multi-key
shape: `endpoint` remains required (✅), `adb` and `apk_package` remain optional
extensions, and absence of extension keys causes graceful degradation (UI scenarios
skipped) but MUST NOT fail accept env-up.

#### Scenario: HELMREDO-S4 §5 endpoint uses cluster DNS format

- **GIVEN** the cookbook endpoint JSON section
- **WHEN** the section body is read
- **THEN** the body contains the literal string `svc.cluster.local` in the endpoint
  value AND the body labels `endpoint` as required (marker ✅ or the word
  "必需" / "required") AND `adb` / `apk_package` as optional extensions

### Requirement: cookbook §6 Makefile 范本 MUST 使用 helm upgrade 而非 docker compose

The `## 6` Makefile section SHALL provide a copy-pasteable Makefile block containing
both `accept-env-up` and `accept-env-down` recipes where:

- `accept-env-up` calls `helm upgrade --install lab charts/accept-lab` and
  `helm upgrade --install emulator charts/emulator`, then calls
  `./emulator/boot-wait-k8s.sh` (or equivalent inline kubectl exec loop), then
  obtains the emulator ADB ClusterIP via `kubectl ... get svc emulator-adb`,
  then calls `./apk/build.sh`, then `adb install -r`, then emits the endpoint
  JSON on the last stdout line via `printf`.
- All progress / log lines are routed to stderr (`>&2`).
- The terminating `printf` format string contains `endpoint`, `adb`,
  `apk_package`, `namespace` and ends with `\n`.
- `accept-env-down` calls `helm uninstall emulator`, `helm uninstall lab`, and
  `kubectl delete namespace`, each prefixed with `-` (make ignore-error) or
  appended with `|| true`, so it is best-effort idempotent.

#### Scenario: HELMREDO-S5 §6 Makefile accept-env-up calls helm upgrade for backend and emulator

- **GIVEN** the cookbook §6 Makefile sample
- **WHEN** the `accept-env-up` recipe body is parsed
- **THEN** the body contains at least two invocations of `helm upgrade --install`
  (one for the backend chart, one for the emulator chart) AND contains
  `boot-wait-k8s.sh` or an inline `kubectl exec` boot-completed loop

#### Scenario: HELMREDO-S6 §6 Makefile accept-env-up printf covers four endpoint keys ending with \n

- **GIVEN** the cookbook §6 Makefile sample `accept-env-up` recipe
- **WHEN** the final `printf` command is inspected
- **THEN** the format string contains `endpoint`, `adb`, `apk_package`,
  `namespace` AND ends with `\n`

#### Scenario: HELMREDO-S7 §6 Makefile accept-env-down is best-effort idempotent

- **GIVEN** the cookbook §6 Makefile sample `accept-env-down` recipe
- **WHEN** the recipe body is parsed
- **THEN** every primary teardown command (`helm uninstall emulator`,
  `helm uninstall lab`, `kubectl delete namespace`) is either prefixed with
  `-` (make ignore-error) or appended with `|| true`
