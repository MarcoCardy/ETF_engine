import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from perpetual_engine.data_sources import fetch_url, freeze_bytes


URL = "https://example.test/artifacts/source.csv"
NOW = datetime(2026, 8, 22, 10, 30, tzinfo=timezone.utc)


class DataSourceTests(TestCase):
    def test_fetch_url_supplies_a_verified_tls_context(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return b"downloaded"

        def urlopen(_url, *, context):
            self.assertTrue(context.check_hostname)
            return Response()

        with patch("urllib.request.urlopen", side_effect=urlopen):
            self.assertEqual(fetch_url(URL), b"downloaded")

    def test_freeze_writes_content_and_deterministic_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = freeze_bytes(b"one", URL, NOW, Path(directory), "1")
            self.assertEqual(artifact.source_hash, hashlib.sha256(b"one").hexdigest())
            self.assertEqual(artifact.local_path, Path(directory) / artifact.source_hash / "source.csv")
            self.assertEqual(artifact.local_path.read_bytes(), b"one")
            manifest = artifact.local_path.parent / "manifest.json"
            self.assertEqual(
                manifest.read_bytes(),
                b'{"byte_count":3,"parser_version":"1","retrieved_at":"2026-08-22T10:30:00+00:00","source_hash":"' + artifact.source_hash.encode() + b'","source_url":"https://example.test/artifacts/source.csv"}',
            )
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["source_hash"], artifact.source_hash)

    def test_identical_bytes_reuse_existing_vintage_without_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            first = freeze_bytes(b"one", URL, NOW, raw_dir, "1")
            manifest = first.local_path.parent / "manifest.json"
            original_raw = first.local_path.read_bytes()
            original_manifest = manifest.read_bytes()
            second = freeze_bytes(b"one", URL, NOW, raw_dir, "2")
            self.assertEqual(second.source_hash, first.source_hash)
            self.assertEqual(first.local_path.read_bytes(), original_raw)
            self.assertEqual(manifest.read_bytes(), original_manifest)

    def test_changed_bytes_create_new_vintage(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            first = freeze_bytes(b"one", URL, NOW, raw_dir, "1")
            second = freeze_bytes(b"two", URL, NOW, raw_dir, "1")
            self.assertNotEqual(first.source_hash, second.source_hash)
            self.assertTrue(first.local_path.exists())
            self.assertTrue(second.local_path.exists())

    def test_refreezing_rejects_a_corrupted_existing_raw_file(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            artifact = freeze_bytes(b"one", URL, NOW, raw_dir, "1")
            artifact.local_path.write_bytes(b"corrupted")
            with self.assertRaisesRegex(ValueError, "corrupt"):
                freeze_bytes(b"one", URL, NOW, raw_dir, "1")

    def test_source_named_manifest_json_uses_reserved_raw_namespace(self):
        with tempfile.TemporaryDirectory() as directory:
            source_url = "https://example.test/artifacts/manifest.json"
            artifact = freeze_bytes(b"payload", source_url, NOW, Path(directory), "1")
            manifest_path = artifact.local_path.parent.parent / "manifest.json"
            self.assertEqual(artifact.local_path.parent.name, "raw")
            self.assertEqual(artifact.local_path.name, f"{hashlib.sha256(source_url.encode()).hexdigest()}.bin")
            self.assertEqual(artifact.local_path.read_bytes(), b"payload")
            self.assertNotEqual(artifact.local_path, manifest_path)
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["source_url"], source_url)

    def test_case_insensitive_reserved_names_never_collide_or_leave_partial_artifacts(self):
        reserved_names = ("manifest.json", "MANIFEST.JSON", "provenance", "PROVENANCE", "raw", "RAW")
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            for index, filename in enumerate(reserved_names):
                source_url = f"https://example.test/artifacts/{filename}"
                artifact = freeze_bytes(str(index).encode(), source_url, NOW, raw_dir, "1")
                artifact_dir = raw_dir / artifact.source_hash
                self.assertEqual(artifact.local_path.parent.name, "raw")
                self.assertEqual(artifact.local_path.name, f"{hashlib.sha256(source_url.encode()).hexdigest()}.bin")
                self.assertEqual(
                    {entry.name.casefold() for entry in artifact_dir.iterdir()},
                    {"manifest.json", "provenance", "raw"},
                )
                self.assertTrue((artifact_dir / "manifest.json").is_file())
                self.assertTrue((artifact_dir / "provenance").is_dir())

    def test_windows_trailing_dot_and_space_variants_use_reserved_raw_namespace(self):
        reserved_names = ("manifest.json.", "manifest.json ", "MANIFEST.JSON.", "provenance.", "raw.")
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            for index, filename in enumerate(reserved_names):
                source_url = f"https://example.test/artifacts/{filename}"
                artifact = freeze_bytes(str(index).encode(), source_url, NOW, raw_dir, "1")
                artifact_dir = raw_dir / artifact.source_hash
                self.assertEqual(artifact.local_path.parent.name, "raw")
                self.assertEqual(artifact.local_path.name, f"{hashlib.sha256(source_url.encode()).hexdigest()}.bin")
                self.assertEqual(
                    {entry.name.casefold() for entry in artifact_dir.iterdir()},
                    {"manifest.json", "provenance", "raw"},
                )

    def test_empty_windows_collision_name_is_rejected_before_creating_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            with self.assertRaisesRegex(ValueError, "usable filename"):
                freeze_bytes(b"payload", "https://example.test/artifacts/ ", NOW, raw_dir, "1")
            self.assertEqual(tuple(raw_dir.iterdir()), ())

    def test_dot_only_and_decoded_empty_url_segments_are_rejected_before_writing(self):
        source_urls = (
            "https://example.test/artifacts/.",
            "https://example.test/artifacts/%2E",
            "https://example.test/artifacts/%2E%2E",
            "https://example.test/artifacts/%20",
        )
        for source_url in source_urls:
            with self.subTest(source_url=source_url), tempfile.TemporaryDirectory() as directory:
                raw_dir = Path(directory)
                with self.assertRaisesRegex(ValueError, "usable filename"):
                    freeze_bytes(b"payload", source_url, NOW, raw_dir, "1")
                self.assertEqual(tuple(raw_dir.iterdir()), ())

    def test_decoded_path_separator_in_final_url_segment_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            with self.assertRaisesRegex(ValueError, "filename"):
                freeze_bytes(b"payload", "https://example.test/artifacts/source%2Fother.csv", NOW, raw_dir, "1")
            self.assertEqual(tuple(raw_dir.iterdir()), ())

    def test_windows_reserved_characters_and_controls_are_rejected_before_writing(self):
        source_urls = (
            "https://example.test/artifacts/source%3A.csv",
            "https://example.test/artifacts/%3Csource%3E.csv",
            "https://example.test/artifacts/source%1F.csv",
        )
        for source_url in source_urls:
            with self.subTest(source_url=source_url), tempfile.TemporaryDirectory() as directory:
                raw_dir = Path(directory)
                with self.assertRaisesRegex(ValueError, "invalid filename"):
                    freeze_bytes(b"payload", source_url, NOW, raw_dir, "1")
                self.assertEqual(tuple(raw_dir.iterdir()), ())

    def test_windows_device_names_are_rejected_with_extensions_before_writing(self):
        filenames = ("CON", "CON.txt", "prn.", "AUX ", "nul.dat", "COM1", "com9.csv", "LPT1", "lpt9.txt")
        for filename in filenames:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                raw_dir = Path(directory)
                with self.assertRaisesRegex(ValueError, "reserved device"):
                    freeze_bytes(b"payload", f"https://example.test/artifacts/{filename}", NOW, raw_dir, "1")
                self.assertEqual(tuple(raw_dir.iterdir()), ())

    def test_non_reserved_filename_keeps_its_stable_original_path(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = freeze_bytes(b"payload", "https://example.test/artifacts/Source.CSV", NOW, Path(directory), "1")
            self.assertEqual(artifact.local_path.name, "Source.CSV")
            self.assertNotEqual(artifact.local_path.parent.name.casefold(), "raw")

    def test_same_bytes_from_different_urls_keep_each_artifact_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            first = freeze_bytes(b"one", "https://example.test/one.csv", NOW, raw_dir, "1")
            root_manifest = (raw_dir / first.source_hash / "manifest.json").read_bytes()
            second_time = datetime(2026, 8, 23, 10, 30, tzinfo=timezone.utc)
            second = freeze_bytes(b"one", "https://example.test/two.csv", second_time, raw_dir, "2")
            records = [json.loads(path.read_text(encoding="utf-8")) for path in (raw_dir / first.source_hash / "provenance").glob("*.json")]
            self.assertEqual(first.source_hash, second.source_hash)
            self.assertEqual(first.local_path.read_bytes(), b"one")
            self.assertEqual(second.local_path.read_bytes(), b"one")
            self.assertEqual((raw_dir / first.source_hash / "manifest.json").read_bytes(), root_manifest)
            self.assertCountEqual(
                records,
                [
                    {"byte_count": 3, "parser_version": "1", "retrieved_at": "2026-08-22T10:30:00+00:00", "source_hash": first.source_hash, "source_url": "https://example.test/one.csv"},
                    {"byte_count": 3, "parser_version": "2", "retrieved_at": "2026-08-23T10:30:00+00:00", "source_hash": second.source_hash, "source_url": "https://example.test/two.csv"},
                ],
            )
