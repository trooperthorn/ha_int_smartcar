# Releasing

A merge to `main` is the only release path. Nobody creates a tag or a release
by hand, and nobody edits a version field by hand.

## The invariant

```text
tag == manifest version == the version inside smartcar.zip
```

`custom_components/smartcar/manifest.json` carries the version HACS reads
after it unpacks the asset. The tag carries the version HACS shows in its
list. If the two disagree, HACS offers an update that never settles, which is
the failure this pipeline exists to prevent. One writer,
`scripts/set_version.py`, updates every field named in `.release.json`. One
independent reader, `validate_versions` in
`scripts/build_release_artifacts.py`, refuses to build when the fields
disagree, so a defect in the writer cannot validate itself.

Versions are calendar versions, `YYYY.MM.DD.N` in `America/Chicago`, with the
sequence restarting at 1 each day. Tags carry no `v` prefix, because every
published tag in this repository already uses the bare form and HACS displays
the tag string verbatim.

## What happens on a merge

1. `release.yml` runs on the push to `main`. It calls `test.yml` and
   `validate.yml` through `workflow_call`, so the release runs the exact jobs
   that gated the pull request rather than a second copy that can drift.
2. The `release` job reads the version out of the manifest, refuses to
   continue if a published release already carries that tag, and builds
   `dist/smartcar.zip` deterministically: fixed timestamps, sorted entries,
   fixed modes, `__pycache__` excluded. The archive root is the contents of
   `custom_components/smartcar`, and the asset name stays `smartcar.zip`
   because installed HACS clients already track that name.
3. It generates an SPDX SBOM, a `SHA256SUMS` file, and build provenance and
   SBOM attestations.
4. It creates the tag through the API, opens a draft release, attaches every
   asset, and only then publishes. Immutable releases lock a tag and its
   assets at publication, so a partial run leaves a draft to resume rather
   than a published release that cannot be corrected.
5. `prepare-release.yml` runs after a successful `Release` on `main`. If the
   manifest version is still the one that was just published and a
   release-bearing path changed since that tag, it bumps the manifest to the
   next calendar version on the branch `automation/calver-release`, opens a
   pull request, and enables auto-merge. That merge triggers the next
   `Release`. Release-bearing paths are listed in `.release.json`; changes to
   documentation and tests alone do not produce a bump.

## The GitHub App this needs

`prepare-release.yml` mints a short-lived, repository-scoped token from the
`ha-soc-release-automation` GitHub App rather than using the workflow token,
because a push made with the workflow token does not start the checks that
protected `main` requires.

Two settings are needed on this repository:

| Name | Kind | Value |
| --- | --- | --- |
| `RELEASE_AUTOMATION_CLIENT_ID` | Actions variable | `Iv23lilFnxrLlicdOl0g` |
| `RELEASE_AUTOMATION_PRIVATE_KEY` | Actions secret | the App's private key, held by the maintainer |

The variable is set. The private key is not, and cannot be set by automation.
Until the maintainer adds `RELEASE_AUTOMATION_PRIVATE_KEY`, the
`prepare-release.yml` run fails at the "Verify release-automation credentials
are configured" step, before it changes anything. Nothing is published and
nothing is half-written; the effect is simply that no bump pull request is
opened, so the manifest stays at the last released version and the next merge
to `main` publishes nothing new. Bumping by hand in that state is:

```bash
python3 scripts/set_version.py --next-from-tags
git switch -c chore/release-bump
git commit -am "chore(release): $(python3 scripts/build_release_artifacts.py --validate-only)"
```

then open a pull request and merge it; the merge publishes.

## Verifying a published release

```bash
gh release download <tag> -R trooperthorn/ha_int_smartcar -p smartcar.zip -p SHA256SUMS
sha256sum --check SHA256SUMS --ignore-missing
gh attestation verify smartcar.zip -R trooperthorn/ha_int_smartcar
```

HACS itself does not check attestations or checksums. These assets let an
operator verify a download by hand; they do not make a HACS install verified.
