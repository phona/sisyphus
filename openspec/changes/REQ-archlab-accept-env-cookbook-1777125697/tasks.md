# Tasks — REQ-archlab-accept-env-cookbook-1777125697

## Stage: contract / spec

- [x] 立 `specs/archlab-accept-env-cookbook/spec.md`（openspec delta 格式 ADDED Requirements）
- [x] 9 个 scenario：cookbook 文件存在 / 9 个 section 各自的内容约束 / 多键 endpoint JSON / Makefile 范本 stderr-stdout 分流 / cross-link 到 integration-contracts.md / README.md / CLAUDE.md
- [x] scenario ID 用 `[ARCHLAB-S<N>]` 命名空间防撞

## Stage: implementation

- [x] `docs/cookbook/ttpos-arch-lab-accept-env.md` —— 完整 cookbook（§0 TL;DR / §1 repo 布局 / §2 backend compose / §3 emulator container / §4 APK build / §5 endpoint JSON 多键契约 / §6 完整 Makefile 范本 / §7 accept-agent prompt 段 / §8 排查清单 / §9 跟既有契约的关系）
- [x] `docs/integration-contracts.md` §4.2.2 加 cross-link 引到 cookbook
- [x] `README.md` 文档索引追一行 cookbook 入口
- [x] `CLAUDE.md` 文档索引追一行 cookbook 入口

## Stage: validation

- [x] `openspec validate openspec/changes/REQ-archlab-accept-env-cookbook-1777125697 --strict` 通过
- [x] `scripts/check-scenario-refs.sh` 全 ARCHLAB-S* scenario ID 引用解析
- [x] `make ci-lint` 通过（docs-only diff，scope 后无 *.py 变更）
- [x] `make ci-unit-test` 通过（pytest 套不动）
- [x] `make ci-integration-test` 通过（exit 5 / 0 都接受）

## Stage: PR

- [x] `git push origin feat/REQ-archlab-accept-env-cookbook-1777125697`
- [x] `gh pr create` —— title + body 写清楚动机 + 测试方案
- [x] BKD issue tags 保留 `analyze` + `REQ-archlab-accept-env-cookbook-1777125697`
- [x] BKD issue move review

## 不在本 REQ scope（明确）

- ❌ 改 ttpos-arch-lab 仓代码（cookbook 在 sisyphus 仓里描述，业务仓改造另开 REQ）
- ❌ 改 sisyphus orchestrator / accept-agent prompt 模板（多键 endpoint 是文档约定）
- ❌ 写 `tests/integration/`（M18 challenger-agent 的活；docs-only REQ 也无 test-able contract）
