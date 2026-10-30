"""Capas fijas con nombre versionado por contenido (sin red)."""

import hashlib
import json

from main_nowcast import write_versioned_layer

LAYER = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"id": "A"}, "geometry": None}]}


def test_same_content_keeps_the_same_file_name(tmp_path):
    first = write_versioned_layer(tmp_path, "watersheds_lima", LAYER)
    second = write_versioned_layer(tmp_path, "watersheds_lima", json.loads(json.dumps(LAYER)))
    assert first == second
    assert first["file"].startswith("watersheds_lima-") and first["file"].endswith(".geojson")


def test_changed_content_gets_a_new_name_and_matching_hash(tmp_path):
    first = write_versioned_layer(tmp_path, "exposure_lima", LAYER)
    changed = dict(LAYER, coverage={"state": "PARTIAL"})
    second = write_versioned_layer(tmp_path, "exposure_lima", changed)
    assert second["file"] != first["file"]
    body = (tmp_path / second["file"]).read_bytes()
    assert hashlib.sha256(body).hexdigest() == second["sha256"]
    assert len(body) == second["bytes"]
    assert second["file"] == f"exposure_lima-{second['sha256'][:16]}.geojson"
