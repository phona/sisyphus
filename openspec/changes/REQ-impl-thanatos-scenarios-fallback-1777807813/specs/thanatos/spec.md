## ADDED Requirements

### Requirement: thanatos skill loader resolves repo skill.yaml with .sisyphus/scenarios/ → .thanatos/ fallback

The thanatos skill loader SHALL expose a `resolve_skill_path(repo_root)` function
that locates a repository's `skill.yaml` using a two-step ordered fallback. The
function MUST first check `<repo_root>/.sisyphus/scenarios/`: if that directory
exists and contains at least one entry, the function MUST return
`<repo_root>/.sisyphus/scenarios/skill.yaml`. Otherwise the function MUST fall
back to `<repo_root>/.thanatos/`: if that directory exists, return
`<repo_root>/.thanatos/skill.yaml`. If neither directory exists the function
MUST raise `SkillLoadError`. The resolver MUST NOT open or validate the
returned file — schema validation remains the responsibility of `load_skill`.
The fallback MUST be transparent to the accept-agent and thanatos runner:
neither needs to know which directory was selected. Repositories that ship only
`.thanatos/` MUST continue to work without modification.

#### Scenario: CREO-S32 .sisyphus/scenarios/ takes priority over .thanatos/
- **GIVEN** a repository has both `<repo_root>/.sisyphus/scenarios/skill.yaml` and `<repo_root>/.thanatos/skill.yaml`
- **WHEN** `resolve_skill_path(repo_root)` is called
- **THEN** the returned path is `<repo_root>/.sisyphus/scenarios/skill.yaml`

#### Scenario: CREO-S33 fallback to .thanatos/ when .sisyphus/scenarios/ absent
- **GIVEN** a repository has `<repo_root>/.thanatos/skill.yaml` but no `<repo_root>/.sisyphus/scenarios/` directory
- **WHEN** `resolve_skill_path(repo_root)` is called
- **THEN** the returned path is `<repo_root>/.thanatos/skill.yaml`

#### Scenario: CREO-S34 fallback to .thanatos/ when .sisyphus/scenarios/ is empty
- **GIVEN** a repository has an empty `<repo_root>/.sisyphus/scenarios/` directory and `<repo_root>/.thanatos/skill.yaml`
- **WHEN** `resolve_skill_path(repo_root)` is called
- **THEN** the returned path is `<repo_root>/.thanatos/skill.yaml`

#### Scenario: CREO-S35 neither path exists raises SkillLoadError
- **GIVEN** a repository has neither `<repo_root>/.sisyphus/scenarios/` nor `<repo_root>/.thanatos/`
- **WHEN** `resolve_skill_path(repo_root)` is called
- **THEN** `SkillLoadError` is raised with a message naming both attempted directories
