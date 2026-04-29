# REQ-fixer-cap-5-to-2-1777420814 Tasks

## Stage: spec
- [x] author specs/fixer-round-cap/spec.md with scenarios FRC-S1..S5

## Stage: implementation
- [x] Change `fixer_round_cap` default from 5 to 2 in `config.py`
- [x] Update `docs/IMPACT-REPORT.md` cap reference 5→2
- [x] Update `docs/user-feedback-loop.md` cap reference 5→2

## Stage: tests
- [x] Update `test_contract_fixer_round_cap.py` docstring
- [x] Update `test_verifier.py`: monkeypatch cap=5 for round-counter test + default cap test 5→2
- [x] Update `test_watchdog.py` comment

## Stage: observability
- [x] Add Q19: fixer round distribution SQL
- [x] Add Q20: fixer decision distribution by cap SQL

## Stage: PR
- [x] git push feat/REQ-fixer-cap-5-to-2-1777420814
- [x] gh pr create --label sisyphus
