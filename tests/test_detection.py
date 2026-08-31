"""Detection stack: the MediaPipe Tasks API wiring and the second-formatter.

The bug these guard against: mediapipe >= 0.10.30 removed the whole
`mp.solutions` namespace, the init raised AttributeError, and a bare
`except Exception` swallowed it. Every presence decision silently fell back to
Haar cascade for two months.
"""
from types import SimpleNamespace

import numpy as np
import pytest


def test_mediapipe_loaded(spg):
    """The whole point of the Tasks API migration. Never let this go quiet."""
    assert spg._HAS_MP, f"MediaPipe failed to init: {spg._MP_ERR}"
    assert spg._MP_ERR == ""


def test_face_model_file_exists(spg):
    assert spg.MP_MODEL_FILE.endswith("blaze_face_short_range.tflite")
    import os
    assert os.path.exists(spg.MP_MODEL_FILE)


def test_legacy_solutions_api_not_used(spg):
    """`mp.solutions.face_detection` is gone from mediapipe; don't reintroduce it."""
    import os
    src = open(os.path.join(spg._DIR, "main.py"), encoding="utf-8-sig").read()
    assert "mp.solutions" not in src


def test_setlastinputinfo_never_called(spg):
    """SetLastInputInfo is documented but not exported by user32 at all.

    Calling it raised AttributeError on every invocation. It may appear in a
    comment explaining that, but never as an actual call.
    """
    import os
    src = open(os.path.join(spg._DIR, "main.py"), encoding="utf-8-sig").read()
    assert "user32.SetLastInputInfo" not in src
    assert "windll.kernel32.SetLastInputInfo" not in src


def test_detect_boxes_reads_tasks_api_fields(spg, monkeypatch):
    """Tasks API gives absolute pixels on `bounding_box`, not relative floats.

    Built from the real mediapipe container classes so a field rename upstream
    breaks this test rather than production.
    """
    from mediapipe.tasks.python.components.containers.bounding_box import BoundingBox
    from mediapipe.tasks.python.components.containers.detections import (
        Detection, DetectionResult)

    result = DetectionResult(detections=[
        Detection(bounding_box=BoundingBox(origin_x=100, origin_y=50,
                                           width=80, height=90),
                  categories=[], keypoints=None),
        # a face partly off the left/top edge: origin must clamp to 0
        Detection(bounding_box=BoundingBox(origin_x=-5, origin_y=-3,
                                           width=40, height=40),
                  categories=[], keypoints=None),
    ])
    monkeypatch.setattr(spg, "_mp_detect",
                        SimpleNamespace(detect=lambda img: result))

    boxes = spg._detect_boxes(np.zeros((480, 640, 3), dtype=np.uint8))
    assert boxes == [(100, 50, 80, 90), (0, 0, 40, 40)]


def test_detect_boxes_no_faces(spg, monkeypatch):
    from mediapipe.tasks.python.components.containers.detections import DetectionResult
    monkeypatch.setattr(
        spg, "_mp_detect",
        SimpleNamespace(detect=lambda img: DetectionResult(detections=[])))
    assert spg._detect_boxes(np.zeros((480, 640, 3), dtype=np.uint8)) == []


@pytest.mark.parametrize("secs,expected", [
    (5, "5s"), (30, "30s"), (59, "59s"),
    (60, "1:00"), (90, "1:30"), (120, "2:00"),
    (600, "10:00"), (895, "14:55"), (900, "15:00"),
])
def test_fmt_sec(spg, secs, expected):
    assert spg._fmt_sec(secs) == expected
