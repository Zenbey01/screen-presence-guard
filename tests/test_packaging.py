"""Repo and build invariants. No GUI, no Tk — these run anywhere."""
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    return open(os.path.join(REPO, *parts), encoding="utf-8-sig").read()


def test_face_model_is_committed():
    """The bundle needs it and `--add-data` cannot ship what is not in the repo."""
    p = os.path.join(REPO, "blaze_face_short_range.tflite")
    assert os.path.exists(p)
    assert os.path.getsize(p) > 100_000


def test_build_adds_both_data_files():
    """Dropping either one produces a build that silently falls back to Haar."""
    build = read("build.ps1")
    assert '--add-data "icon.ico;."' in build
    assert '--add-data "blaze_face_short_range.tflite;."' in build


def test_build_verifies_the_bundle():
    """The gate that stops a model-less zip from ever being published."""
    build = read("build.ps1")
    assert "_internal\\blaze_face_short_range.tflite" in build
    assert "exit 1" in build


def test_installer_points_the_icon_at_the_exe():
    """icon.ico lands in _internal/, so `%DIR%icon.ico` resolved to nothing and
    recipients got a blank Desktop shortcut."""
    build = read("build.ps1")
    assert "IconLocation='%DIR%ScreenPresenceGuard.exe,0'" in build


def test_requirements_cover_the_imports():
    reqs = read("requirements.txt").lower()
    for pkg in ("opencv-contrib-python", "mediapipe", "customtkinter",
                "pillow", "pystray"):
        assert pkg in reqs, f"{pkg} missing from requirements.txt"


def test_workflow_has_no_powershell_here_strings():
    """`'@` and `"@` must sit at column 0, which YAML block indentation makes
    impossible. A here-string here is a guaranteed runtime parse error."""
    wf = read(".github", "workflows", "build.yml")
    for lineno, line in enumerate(wf.splitlines(), 1):
        assert not re.search(r"=\s*@[\"']", line), \
            f"here-string opened at line {lineno} of build.yml"


def test_workflow_release_step_tolerates_nonzero_exits():
    """Actions runs pwsh with $ErrorActionPreference='stop' and PS 7.4+ turns a
    non-zero native exit into a terminating error, so probing for an existing
    release aborts the step unless the preference is relaxed."""
    wf = read(".github", "workflows", "build.yml")
    assert "$ErrorActionPreference = 'Continue'" in wf
    assert "LASTEXITCODE" in wf


def test_release_notes_state_windows_only():
    """A macOS user should not download 140 MB to discover ctypes.windll."""
    notes = read(".github", "RELEASE_NOTES.md")
    assert "Windows" in notes


def test_docs_are_identical():
    """CLAUDE.md and AGENTS.md are the same document for two tools."""
    assert read("CLAUDE.md") == read("AGENTS.md")


def test_personal_face_data_is_ignored():
    ignore = read(".gitignore")
    assert "face_model.yml" in ignore
    assert "face_imgs.pkl" in ignore


@pytest.mark.parametrize("name", ["dist/", "ScreenPresenceGuard.zip"])
def test_build_output_is_ignored(name):
    assert name in read(".gitignore")
