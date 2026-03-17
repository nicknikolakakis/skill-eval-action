# Skill Eval Action

A GitHub Action that evaluates [Claude Code skills](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf) against YAML test cases with automated grading and PR reporting.

## Usage

```yaml
- uses: nicknikolakakis/skill-eval-action@v1
  with:
    skill-name: tf-guide
    skill-path: ./skills/tf-guide
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Full example with matrix strategy

```yaml
name: Skill Eval
on:
  pull_request:
    paths:
      - 'skills/**'

permissions:
  contents: read
  pull-requests: write    # for PR comments

jobs:
  eval:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        skill:
          - tf-guide
          - k8s-operator-sdk
          - secure-gh-workflow
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4

      - uses: nicknikolakakis/skill-eval-action@v1
        with:
          skill-name: ${{ matrix.skill }}
          skill-path: skills/${{ matrix.skill }}
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          pass-threshold: '80'
```

## Inputs

| Input | Required | Default | Description |
|-------|:--------:|---------|-------------|
| `skill-name` | Yes | - | Name of the skill to evaluate |
| `skill-path` | Yes | - | Path to the skill directory (must contain `SKILL.md` and `evals/`) |
| `anthropic-api-key` | Yes | - | Anthropic API key for the `claude` CLI |
| `pass-threshold` | No | `80` | Minimum pass rate (0-100) to succeed |
| `timeout` | No | `120` | Timeout per eval case in seconds |
| `post-comment` | No | `true` | Post results as a PR comment |
| `github-token` | No | `${{ github.token }}` | Token for PR comments |
| `upload-viewer` | No | `true` | Upload eval-viewer HTML as an artifact |

## Outputs

| Output | Description |
|--------|-------------|
| `pass-rate` | Overall pass rate as percentage (0-100) |
| `passed` | Total criteria passed |
| `total` | Total criteria evaluated |
| `cases-run` | Number of eval cases executed |

## How it works

```
eval YAML → claude -p (execute) → claude -p (grade) → summary.json → PR comment + artifact
```

1. **Discovers** eval YAML files in `<skill-path>/evals/`
2. **Executes** each case via `claude -p` with skill content injected
3. **Grades** each response against criteria via a separate `claude -p` call
4. **Aggregates** results and writes a GitHub Actions step summary
5. **Posts** a PR comment with pass/fail table and failed criteria details
6. **Uploads** an interactive eval viewer as an artifact
7. **Fails** the step if pass rate is below threshold

## Eval case format

Place YAML files in `<skill-path>/evals/`:

```yaml
# evals/001-basic-usage.yaml
name: Basic usage
prompt: "The user prompt that should trigger and test this skill"
files:                          # optional — temp files created before the test
  - path: "main.tf"
    content: |
      resource "aws_instance" "web" {}
criteria:                       # success criteria — ALL must pass
  - "Output contains a valid resource block"
  - "Uses for_each, not count, for multiple resources"
expect_skill: true              # optional — default true
timeout: 120                    # optional — default from action input
```

Include at least one negative trigger case (`expect_skill: false`).

## PR comment

The action posts (or updates) a PR comment with:

- Pass/fail table with per-case results
- Collapsible failed criteria with evidence
- Eval metadata (time, tokens, threshold)

Comments are upserted using an HTML marker — re-runs update the existing comment instead of creating duplicates.

## Cost considerations

Each eval case makes **2 API calls** (execute + grade). A skill with 5 cases = 10 calls per run. With matrix strategy across multiple skills, costs multiply. Set appropriate `timeout` values to limit runaway token usage.

## Requirements

- `ANTHROPIC_API_KEY` as a repository secret
- Eval YAML files in the skill's `evals/` directory
- Skills must follow the [Agent Skills](https://agentskills.io/specification) format

## License

MIT
