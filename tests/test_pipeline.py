import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "pipeline", Path(__file__).resolve().parents[1] / "scripts/build_cameras.py"
)
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


def camera(kind="speed", limit=60):
    return {
        "lat": 41.3, "lon": 69.2, "type": kind, "limit": limit,
        "bearing": [], "name": None, "src": "manual",
    }


class PipelineTests(unittest.TestCase):
    def test_distinct_types_are_not_merged(self):
        result = pipeline.dedupe([camera(), camera("seatbelt")])
        self.assertEqual(len(result), 2)

    def test_same_type_duplicates_merge(self):
        self.assertEqual(len(pipeline.dedupe([camera(), camera()])), 1)

    def test_type_ids_do_not_collide(self):
        types = ["speed", "seatbelt", "average", "alpr", "traffic",
                 "mobile", "redlight", "buslane"]
        self.assertEqual(len({pipeline.stable_id(camera(t)) for t in types}), len(types))

    def test_metadata_update_and_unchanged_version(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "cameras.json"
            changes = Path(directory) / "changes.json"
            with patch.multiple(pipeline, OUT=out, CHANGES=changes), \
                 patch.object(pipeline, "from_official", return_value=[]), \
                 patch.object(pipeline, "from_overpass", return_value=[]), \
                 patch.object(pipeline, "from_uzbdb", return_value=[]), \
                 contextlib.redirect_stdout(io.StringIO()):
                with patch.object(pipeline, "from_manual", side_effect=lambda: [camera(limit=60)]):
                    pipeline.main()
                with patch.object(pipeline, "from_manual", side_effect=lambda: [camera(limit=80)]):
                    pipeline.main()
                    updated = out.read_text()
                    pipeline.main()
                self.assertEqual(out.read_text(), updated)
                result = json.loads(updated)
                self.assertEqual(result["version"], 2)
                self.assertEqual(result["cameras"][0]["limit"], 80)
                self.assertEqual(json.loads(changes.read_text())["added"], 0)


if __name__ == "__main__":
    unittest.main()
