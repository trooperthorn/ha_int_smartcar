# Decisions

Dated decisions, each with the alternative that was rejected and why.

## 2026-09-22: release-drafter removed in favor of merge-driven CalVer releases

`release-drafter` ran on every push to `main` and failed on every one of
them. It parses tags as semantic versions, so the calendar tags this
repository publishes (`2026.09.22.1`) did not parse and it resolved the next
version as `v0.1.0`. It then failed again with "Resource not accessible by
integration", because the workflow declared no `contents: write` permission
and release-drafter creates a draft release. The workflow therefore produced
nothing but a red run on main, and every release was actually created by hand
with `gh release create`.

The hand-driven path had a second defect. The manifest version on `main`
stayed at `0.1.0`, and `.github/update_hacs_manifest.py` patched the version
only inside the release asset at packaging time. The tree and the artifact
disagreed, so nothing but the packaging script enforced the invariant HACS
depends on, and a release built any other way would have shipped `0.1.0`.

The replacement is the release and security baseline used across the other
Home Assistant repositories: the manifest on `main` holds the real version, a
merge to `main` publishes it, and `prepare-release.yml` opens the next bump.
Release notes come from `--generate-notes`, which builds them from the
squash-merged pull request titles that `pr-lint` already forces to be
conventional commits. That covers what release-drafter was configured to
produce, without a second version scheme.

Rejected alternatives:

- Teach release-drafter the calendar scheme with `version-resolver` and a
  `version-template`. It would still be a second place that decides a version,
  competing with the manifest, and the invariant would still be enforced
  nowhere.
- Keep `release.yaml` firing on `release: published` and keep creating
  releases by hand. This keeps a human in the path of the version invariant,
  which is exactly where the `0.1.0` drift came from.

`.github/release-drafter.yml`, `.github/workflows/release-drafter.yaml`,
`.github/workflows/release.yaml`, and `.github/update_hacs_manifest.py` were
deleted in the same change.

## 2026-09-22: the HACS asset keeps the name smartcar.zip

`hacs.json` sets `zip_release` with `filename: smartcar.zip`, and installed
clients look for exactly that asset name on each release. The deterministic
archive built by `scripts/build_release_artifacts.py` therefore keeps the
name and keeps the contents of `custom_components/smartcar` as its root, which
is what the previous `zip -r` step produced.
