# Contributing

Thanks for your interest in contributing to skill-eval-action!

## Submitting a pull request

1. [Fork](https://github.com/nicknikolakakis/skill-eval-action/fork) and clone the repository
2. Make your changes
3. Run `ruff check scripts/` and `zizmor action.yml` to lint
4. Push to your fork and [submit a pull request](https://github.com/nicknikolakakis/skill-eval-action/compare)

## Guidelines

- Keep changes focused — one feature or fix per PR
- Update the README if you add or change inputs/outputs
- Pin any new action references to full commit SHAs
- Follow existing code style (Python with type hints, f-strings)

## Releasing

Releases are managed via the `release.yml` workflow:

1. Tag a new version: `git tag v1.0.1 && git push origin v1.0.1`
2. The workflow creates a GitHub Release and updates the `v1` major tag
3. Edit the release on GitHub and check "Publish to Marketplace"
