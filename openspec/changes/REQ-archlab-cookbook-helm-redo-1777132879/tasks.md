# Tasks: REQ-archlab-cookbook-helm-redo-1777132879

## Stage: spec

- [x] author `openspec/changes/REQ-archlab-cookbook-helm-redo-1777132879/proposal.md` — 动因、方案、取舍
- [x] author `openspec/changes/REQ-archlab-cookbook-helm-redo-1777132879/specs/archlab-cookbook-helm-redo/spec.md` — MODIFIED requirements + 7 scenarios

## Stage: implementation

- [x] rewrite `docs/cookbook/ttpos-arch-lab-accept-env.md` — 9 节重写为 helm 路径（§0 TL;DR、§1 repo layout、§2 backend chart、§3 emulator chart、§4 APK build、§5 endpoint JSON、§6 Makefile、§7 accept-agent、§8 排查、§9 关系说明）
- [x] update `docs/integration-contracts.md` §4.2 — 加 cross-link 到 cookbook（helm 路径 mobile lab 完整食谱）
- [x] update `README.md` 文档索引 — 更新 docs/cookbook/ 行描述（去掉 compose，改为 helm/K3s）

## Stage: PR

- [x] git push feat/REQ-archlab-cookbook-helm-redo-1777132879
- [x] gh pr create
