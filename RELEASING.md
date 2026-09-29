# Releasing

A version tag on `main` publishes `squalor-slicer` to PyPI. The tag must be `v`
plus `slicer.__version__` from `src/slicer/__init__.py`. `.github/workflows/publish.yml`
runs the tests, refuses any other tag, builds the package, and uploads it.
There is no API token. Later releases reuse the same publisher.

Pushing the tag is a separate step. Do it from the commit the release should be,
after the GitHub environment below exists.

## First publish

The PyPI account and the trusted publisher are configured. The publisher is:

- PyPI project name: `squalor-slicer`
- Owner: `squalor-xyz`
- Repository: `slicer`
- Workflow: `publish.yml`
- Environment: `pypi`

The GitHub environment must be named exactly `pypi` and must hold no secrets.
Create it under the repository's Settings → Environments if it is missing. A
blank environment name on the publisher, or any other name, does not match the
workflow.

## Checklist

From a clean `main`, with full git history (`slicer verify` reads it):

```sh
python3 -m unittest discover -s tests -t tests
PYTHONPATH=src python3 -m slicer check
PYTHONPATH=src python3 -m slicer verify
```

Move the `[Unreleased]` notes in `CHANGELOG.md` into a section for the version
you are releasing, and leave `[Unreleased]` empty.

Set `__version__` in `src/slicer/__init__.py` to that version. `pyproject.toml`
reads the attribute. Do not write a static version there.

Commit. Then tag and push the tag for the version you just set:

```sh
VERSION=$(PYTHONPATH=src python3 -c 'import slicer; print(slicer.__version__)')
git tag "v$VERSION"
git push origin "v$VERSION"
```

Watch the Publish workflow. When it is green, the package is at
<https://pypi.org/project/squalor-slicer/>. `pip install squalor-slicer` then
`slicer --version` should print that version. A version number cannot be
uploaded again.
