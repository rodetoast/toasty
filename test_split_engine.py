"""Run pure/mock tests normally; add --integration for real temp-copy CLI tests."""

import argparse
import os
import sys
import copy
import json
import shutil
import subprocess
import struct
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import split_engine as engine

TEST_MAX_FILE_SIZE = 2_000_000


def tags(count=6, length=10):
    return [dict(name=f"Tag{i}", first=i * length + 1, last=(i + 1) * length,
                 direction=0, repeats=i % 2, color=[i, 0, 0, 255], data="", properties=[])
            for i in range(count)]


def snapshot(tag_list):
    count = tag_list[-1]["last"]
    frames = [50 + i for i in range(count)]
    cels = [dict(frame=i + 1, layer=1, data="", color=[0, 0, 0, 0], properties=[])
            for i in range(count)]
    return dict(api=41, version="mock", width=1, height=1, colorMode="0", data="", color=[0, 0, 0, 0],
                properties=[], frames=frames, tags=tag_list, layers=[dict(name="Layer", index=1)],
                palettes=[], cels=cels, slices=0, tilesets=0,
                binary=dict(header="mock header", static={},
                            frames=[dict(duration=f, cels=[dict(pixels=f"pixel-{i}")])
                                    for i, f in enumerate(frames)]))


class MockBackend:
    """Mock sizes never pass to Aseprite; all mock files live in temporary folders."""
    def __init__(self, tag_list=None, bytes_per_tag=700_000, fail_at=None, corrupt=False):
        self.original = snapshot(tag_list if tag_list is not None else tags())
        self.bytes_per_tag = bytes_per_tag
        self.fail_at = fail_at
        self.corrupt = corrupt
        self.candidates = {}
        self.calls = []

    def inspect(self, path, work):
        return copy.deepcopy(self.candidates.get(str(path), self.original))

    def extract(self, source, output, first, last, work):
        self.calls.append((first, last))
        if len(self.calls) == self.fail_at:
            raise engine.SplitFailure("save_failed", "Injected save failure", stdout="mock stdout", stderr="mock stderr")
        original = self.original
        actual = copy.deepcopy(original)
        selected = [t for t in original["tags"] if first <= t["first"] and t["last"] <= last]
        actual["tags"] = [dict(t, first=t["first"] - first + 1, last=t["last"] - first + 1) for t in selected]
        actual["frames"] = original["frames"][first - 1:last]
        actual["binary"]["frames"] = original["binary"]["frames"][first - 1:last]
        actual["cels"] = [dict(c, frame=c["frame"] - first + 1)
                          for c in original["cels"] if first <= c["frame"] <= last]
        if self.corrupt:
            actual["tags"][0]["repeats"] = 99
        self.candidates[str(output)] = actual
        with output.open("wb") as stream:
            stream.truncate(len(selected) * self.bytes_per_tag)


class PaletteTests(unittest.TestCase):
    def legacy(self, colors):
        return struct.pack("<HBB", 1, 0, len(colors) % 256) + bytes(c for rgb in colors for c in rgb)

    def modern(self, colors, name=""):
        payload = struct.pack("<III", len(colors), 0, len(colors) - 1) + bytes(8)
        for rgb in colors:
            payload += struct.pack("<HBBBB", bool(name), *rgb, 255)
            if name:
                encoded = name.encode("utf-8")
                payload += struct.pack("<H", len(encoded)) + encoded
        return payload

    def manifest(self, frames):
        header = bytearray(128)
        struct.pack_into("<HHHHHI", header, 4, 0xA5E0, len(frames), 1, 1, 32, 1)
        body = b""
        for chunks in frames:
            data = b"".join(struct.pack("<IH", len(p) + 6, k) + p for k, p in chunks)
            body += struct.pack("<IHHHHI", len(data) + 16, 0xF1FA, len(chunks), 100, 0, 0) + data
        struct.pack_into("<I", header, 0, len(header) + len(body))
        with tempfile.TemporaryDirectory(prefix="atm_palette_test_") as folder:
            path = Path(folder) / "fixture.aseprite"
            path.write_bytes(header + body)
            return engine.read_binary_manifest(path)

    def test_legacy_and_modern_colors_equivalent(self):
        colors = [(1, 2, 3), (254, 100, 0)]
        legacy = self.manifest([[(0x0004, self.legacy(colors))]])
        modern = self.manifest([[(0x2019, self.modern(colors))]])
        self.assertEqual(legacy["static"], modern["static"])
        changed = self.manifest([[(0x0004, self.legacy([(2, 2, 3), colors[1]]))]])
        self.assertNotEqual(legacy["static"], changed["static"])

    def test_256_colors_and_multiple_packets(self):
        colors = [(i, i, i) for i in range(256)]
        expected = engine.read_palette_chunk(0x0004, self.legacy(colors))
        self.assertEqual(len(expected), 256)
        packets = struct.pack("<H", 2) + self.legacy(colors[:128])[2:] + self.legacy(colors[128:])[2:]
        self.assertEqual(engine.read_palette_chunk(0x0004, packets), expected)

    def test_modern_overrides_legacy_and_preserves_names(self):
        colors = [(1, 2, 3)]
        modern = self.modern(colors, "named color")
        actual = self.manifest([[(0x0004, self.legacy([(99, 99, 99)])), (0x2019, modern)]])
        self.assertEqual(actual["static"], self.manifest([[(0x2019, modern)]])["static"])
        self.assertEqual(actual["static"]["palette"][0][0][-1], "named color")

    def test_reject_malformed_partial_and_animated_palettes(self):
        valid = self.legacy([(1, 2, 3)])
        for payload in (valid[:-1], valid + b"x", struct.pack("<HBB", 1, 1, 1) + b"abc",
                        struct.pack("<HBB", 1, 1, 0) + bytes(768)):
            with self.subTest(payload=payload[:4]), self.assertRaises(engine.SplitFailure):
                self.manifest([[(0x0004, payload)]])
        for frames in ([[(0x0004, valid)], [(0x0004, valid)]],
                       [[(0x0004, valid), (0x0004, valid)]]):
            with self.assertRaises(engine.SplitFailure):
                self.manifest(frames)


class LogicTests(unittest.TestCase):
    def test_size_boundary(self):
        self.assertEqual(engine.MAX_FILE_SIZE, 10_000_000)
        self.assertFalse(engine.needs_split(9_999_999))
        self.assertFalse(engine.needs_split(10_000_000))
        self.assertTrue(engine.needs_split(10_000_001))
        self.assertTrue(engine.needs_split(2_000_001, TEST_MAX_FILE_SIZE))

    def test_automatic_boundary(self):
        self.assertEqual(engine.nearest_boundary(tags(), 0, 6), 3)
        uneven = [dict(name="a", first=1, last=1), dict(name="b", first=2, last=90),
                  dict(name="c", first=91, last=100)]
        self.assertEqual(engine.nearest_boundary(uneven, 0, 3), 2)

    def test_automatic_recursion_above_4mb(self):
        seen = []
        def measure(a, b):
            seen.append((a, b))
            return (b - a) * 700_000
        result = engine.automatic_ranges(tags(), measure, max_size=TEST_MAX_FILE_SIZE, initial_size=4_200_000)
        self.assertGreaterEqual(len(result), 3)
        self.assertEqual([i for a, b in result for i in range(a, b)], list(range(6)))
        self.assertTrue(all((b-a)*700_000 <= TEST_MAX_FILE_SIZE for a, b in result))
        self.assertLessEqual(len(seen), 2 * 6 - 1)

    def test_manual_single_multiple_and_duplicate_boundaries(self):
        self.assertEqual(engine.manual_ranges(55, [30]), [(0, 30), (30, 55)])
        self.assertEqual(engine.manual_ranges(60, [40, 20, 20]), [(0, 20), (20, 40), (40, 60)])
        self.assertEqual(engine.manual_ranges(55, [1, 54]), [(0, 1), (1, 54), (54, 55)])

    def test_invalid_boundaries(self):
        for value in ([], [0], [6], [-1], [True], [1.5], ["2"], None):
            with self.subTest(value=value), self.assertRaises(engine.SplitFailure) as caught:
                engine.manual_ranges(6, value)
            self.assertEqual(caught.exception.status, "invalid_boundary")

    def test_tag_validation(self):
        engine.validate_tags(60, tags())
        for bad, count, status in [([], 60, "no_tags"),
                                   ([dict(name="a", first=1, last=20), dict(name="b", first=20, last=60)], 60, "unsupported_structure"),
                                   ([dict(name="a", first=1, last=20), dict(name="b", first=22, last=60)], 60, "unsupported_structure"),
                                   (tags(), 61, "unsupported_structure")]:
            with self.subTest(bad=bad), self.assertRaises(engine.SplitFailure) as caught:
                engine.validate_tags(count, bad)
            self.assertEqual(caught.exception.status, status)

    def test_single_tag_too_large(self):
        with self.assertRaises(engine.SplitFailure) as caught:
            engine.automatic_ranges(tags(1), lambda a, b: 10_000_001)
        self.assertEqual(caught.exception.status, "single_tag_too_large")
        self.assertEqual(caught.exception.details["bytes"], 10_000_001)
        self.assertEqual(caught.exception.details["tag_name"], "Tag0")

    def test_single_tag_uses_saved_size_not_original_size(self):
        self.assertEqual(engine.automatic_ranges(tags(1), lambda a, b: 1_900_000,
                                                 max_size=TEST_MAX_FILE_SIZE, initial_size=4_200_000), [(0, 1)])
        with self.assertRaises(engine.SplitFailure) as caught:
            engine.automatic_ranges(tags(1), lambda a, b: 2_100_000, max_size=TEST_MAX_FILE_SIZE, initial_size=4_200_000)
        self.assertEqual(caught.exception.details["bytes"], 2_100_000)

    def test_termination_bad_boundary_and_measurement(self):
        with patch.object(engine, "nearest_boundary", return_value=0):
            with self.assertRaises(engine.SplitFailure):
                engine.automatic_ranges(tags(), lambda a, b: 3_000_000, max_size=TEST_MAX_FILE_SIZE)
        with self.assertRaises(engine.SplitFailure):
            engine.automatic_ranges(tags(), lambda a, b: 0)

    def test_names_preserve_phase_and_extension(self):
        self.assertEqual(engine.output_name("Bedivere_Phase1.aseprite", 3), "Bedivere_Phase1_3.aseprite")
        self.assertEqual(engine.output_name("Bedivere.ase", 2), "Bedivere_2.ase")

    def test_preview_is_unmeasured_and_index_based(self):
        plan = engine.preview_plan("Sample.ase", tags(), 60, mode="manual", boundaries=[2, 4])
        self.assertFalse(plan["provisional"])
        self.assertEqual([f["tag_range"] for f in plan["files"]], [[1, 2], [3, 4], [5, 6]])
        self.assertTrue(all(f["bytes"] is None for f in plan["files"]))
        self.assertTrue(engine.preview_plan("Sample.ase", tags(), 60)["provisional"])

    def test_duplicate_tag_names_use_indices(self):
        items = tags()
        for tag in items:
            tag["name"] = "Same"
        engine.validate_tags(60, items)
        self.assertEqual(engine.manual_ranges(6, [2, 4]), [(0, 2), (2, 4), (4, 6)])


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atm_split_mock_")
        self.root = Path(self.temp.name)
        self.source = self.root / "Example_Phase1.aseprite"
        with self.source.open("wb") as stream:
            stream.truncate(4_200_000)
        self.before = engine.sha256(self.source)
        self.destination = self.root / "output"

    def tearDown(self):
        self.assertEqual(engine.sha256(self.source), self.before)
        self.temp.cleanup()

    def run_split(self, backend=None, **kwargs):
        kwargs.setdefault("max_size", TEST_MAX_FILE_SIZE)
        return engine.split_file(self.source, backend=backend or MockBackend(), output_dir=self.destination, **kwargs)

    def test_auto_success_multiple_outputs(self):
        result = self.run_split()
        self.assertEqual(result.status, "auto_success", result)
        self.assertGreaterEqual(len(result.outputs), 3)
        self.assertEqual([Path(o["path"]).name for o in result.outputs],
                         [engine.output_name(self.source, i) for i in range(1, len(result.outputs) + 1)])
        self.assertTrue(all(Path(o["path"]).exists() for o in result.outputs))
        self.assertTrue(result.original_unchanged)
        self.assertEqual([i for o in result.outputs for i in range(o["tag_range"][0], o["tag_range"][1] + 1)],
                         list(range(1, 7)))
        self.assertTrue(all(o["bytes"] <= TEST_MAX_FILE_SIZE for o in result.outputs))

    def test_default_10mb_inspection_and_auto_boundary(self):
        for size in (9_999_999, 10_000_000, 10_000_001):
            with self.subTest(size=size):
                with self.source.open("wb") as stream:
                    stream.truncate(size)
                self.before = engine.sha256(self.source)
                backend = MockBackend()
                inspected = engine.inspect_file(self.source, "mock", backend=backend)
                expected = "ready" if size > 10_000_000 else "not_needed"
                self.assertEqual(inspected.status, expected)
                result = engine.split_file(self.source, backend=backend, output_dir=self.destination)
                self.assertEqual(result.status, "auto_success" if size > 10_000_000 else "not_needed")
                if size <= 10_000_000:
                    self.assertEqual(backend.calls, [])
                    self.assertFalse(self.destination.exists())
                else:
                    self.assertTrue(all(o["bytes"] <= 10_000_000 for o in result.outputs))

    def test_default_10mb_outputs_and_manual_boundaries(self):
        with self.source.open("wb") as stream:
            stream.truncate(18_000_000)
        self.before = engine.sha256(self.source)
        result = engine.split_file(self.source, backend=MockBackend(bytes_per_tag=3_000_000),
                                   output_dir=self.destination)
        self.assertEqual(result.status, "auto_success", result)
        self.assertEqual([o["bytes"] for o in result.outputs], [9_000_000, 9_000_000])
        backend = MockBackend(bytes_per_tag=3_000_000)
        manual_output = self.root / "manual"
        result = engine.split_file(self.source, backend=backend, mode="manual", boundaries=[4],
                                   output_dir=manual_output)
        self.assertEqual(result.status, "manual_oversize", result)
        self.assertEqual(backend.calls, [(1, 40), (41, 60)])
        self.assertFalse(manual_output.exists())

    def test_default_10mb_single_tag_blocks_publication(self):
        with self.source.open("wb") as stream:
            stream.truncate(10_000_001)
        self.before = engine.sha256(self.source)
        result = engine.split_file(self.source, backend=MockBackend(tags(1), bytes_per_tag=10_000_001),
                                   output_dir=self.destination)
        self.assertEqual(result.status, "single_tag_too_large", result)
        self.assertFalse(self.destination.exists())

    def test_manual_multiple_success(self):
        result = self.run_split(mode="manual", boundaries=[4, 2, 2])
        self.assertEqual(result.status, "manual_success", result)
        self.assertEqual([o["tag_range"] for o in result.outputs], [[1, 2], [3, 4], [5, 6]])

    def test_duplicate_names_survive_index_based_extraction(self):
        items = tags()
        for tag in items:
            tag["name"] = "Same"
        result = self.run_split(MockBackend(items), mode="manual", boundaries=[2, 4])
        self.assertEqual(result.status, "manual_success", result)

    def test_manual_oversize_does_not_change_boundaries_or_publish(self):
        backend = MockBackend()
        result = self.run_split(backend, mode="manual", boundaries=[3])
        self.assertEqual(result.status, "manual_oversize")
        self.assertEqual([o["tag_range"] for o in result.details["oversized_ranges"]], [[1, 3], [4, 6]])
        self.assertEqual(backend.calls, [(1, 30), (31, 60)])
        self.assertFalse(self.destination.exists())

    def test_single_tag_failure_no_publication(self):
        result = self.run_split(MockBackend(tags(1), bytes_per_tag=2_000_001))
        self.assertEqual(result.status, "single_tag_too_large")
        self.assertEqual(result.details["bytes"], 2_000_001)
        self.assertFalse(self.destination.exists())

    def test_skip_without_backend(self):
        self.source.write_bytes(b"small")
        self.before = engine.sha256(self.source)
        result = engine.split_file(self.source)
        self.assertEqual(result.status, "not_needed")
        self.assertFalse(self.destination.exists())

    def test_empty_input_is_not_a_successful_skip(self):
        self.source.write_bytes(b"")
        self.before = engine.sha256(self.source)
        self.assertEqual(engine.split_file(self.source).status, "invalid_input")

    def test_readonly_inspection_and_preview_make_no_outputs(self):
        result = engine.inspect_file(self.source, "mock", backend=MockBackend(), include_small=True)
        self.assertEqual(result.status, "not_needed", result)
        engine.preview_plan(result.source, result.details["tags"], result.details["frame_count"])
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_small_file_can_report_counts_without_splitting(self):
        self.source.write_bytes(b"mock small input")
        self.before = engine.sha256(self.source)
        result = engine.inspect_file(self.source, "mock", backend=MockBackend(), include_small=True)
        self.assertEqual(result.status, "not_needed")
        self.assertEqual(result.details["tag_count"], 6)
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_preview_digest_blocks_changed_source(self):
        backend = MockBackend()
        result = engine.split_file(self.source, backend=backend, output_dir=self.destination,
                                   max_size=TEST_MAX_FILE_SIZE, expected_sha256="old hash")
        self.assertEqual(result.status, "source_changed")
        self.assertEqual(backend.calls, [])

    def test_input_collection_excludes_only_generated_folders(self):
        def asset(folder, filename):
            folder.mkdir(exist_ok=True)
            path = folder / filename
            path.write_bytes(b"mock asset - never sent to CLI")
            return path
        backup = asset(self.root / ".aseprite_backup", "old.ase")
        generated = asset(self.root / "Hero_split", "Hero_1.aseprite")
        normal = asset(self.root / "Hero_split", "notes.ase")
        # Mixed ordinary content must not be inferred as an output folder.
        self.assertFalse(engine.is_generated_directory(normal.parent))
        normal.unlink()
        normal = asset(self.root / "ordinary_split", "work.ase")
        substring = asset(self.root / "my_split_work", "work.aseprite")
        stage = asset(self.root / "atm_split_example", "candidate.ase")
        (stage.parent / ".atm_split_staging").write_text("staging")
        unmarked = asset(self.root / "atm_split_regular", "work.ase")
        included = engine.collect_input_files([self.root, generated, backup])
        self.assertNotIn(str(generated), included)
        self.assertNotIn(str(backup), included)
        self.assertNotIn(str(stage), included)
        for path in (self.source, normal, substring, unmarked):
            self.assertIn(str(path), included)

    def test_existing_output_untouched(self):
        self.destination.mkdir()
        marker = self.destination / "keep.txt"
        marker.write_text("keep")
        result = self.run_split()
        self.assertEqual(result.status, "output_conflict")
        self.assertEqual(marker.read_text(), "keep")

    def test_partial_save_failure_cleans_staging(self):
        result = self.run_split(MockBackend(fail_at=2))
        self.assertEqual(result.status, "save_failed")
        self.assertIn("tag_range", result.details)
        self.assertEqual(result.details["stdout"], "mock stdout")
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_reopen_failure(self):
        backend = MockBackend()
        original_inspect = backend.inspect
        def inspect(path, work):
            if path.name.startswith("candidate"):
                raise engine.SplitFailure("reopen_failed", "Injected reopen failure")
            return original_inspect(path, work)
        backend.inspect = inspect
        result = self.run_split(backend)
        self.assertEqual(result.status, "reopen_failed")
        self.assertFalse(self.destination.exists())

    def test_metadata_corruption_blocks_publication(self):
        result = self.run_split(MockBackend(corrupt=True))
        self.assertEqual(result.status, "verification_failed")
        self.assertIn("tags", result.details["fields"])
        self.assertFalse(self.destination.exists())

    def test_publish_time_conflict(self):
        real_publish = engine.publish_directory
        def create_conflict(staging, destination):
            destination.mkdir()
            (destination / "keep.txt").write_text("keep")
            return real_publish(staging, destination)
        with patch.object(engine, "publish_directory", side_effect=create_conflict):
            result = self.run_split()
        self.assertEqual(result.status, "output_conflict")
        self.assertEqual(list(self.destination.iterdir()), [self.destination / "keep.txt"])

    def test_workspace_cleanup_failure_must_not_publish(self):
        real_workspace = engine.staging_workspace
        @contextmanager
        def exit_failure(parent):
            with real_workspace(parent) as work:
                yield work
            raise PermissionError("Injected workspace cleanup failure")
        with patch.object(engine, "staging_workspace", exit_failure):
            result = self.run_split()
        self.assertEqual(result.status, "io_failed")
        self.assertFalse(self.destination.exists(), "Failed workspace cleanup exposed final results")

    def test_staging_permission_failure_no_outputs(self):
        with patch.object(engine, "staging_workspace", side_effect=PermissionError("No write permission")):
            result = self.run_split()
        self.assertEqual(result.status, "io_failed")
        self.assertFalse(self.destination.exists())

    def test_publish_rename_failure_no_outputs(self):
        real_rename = os.rename
        def rename(source, target):
            if Path(target) == self.destination:
                raise PermissionError("Injected final rename failure")
            return real_rename(source, target)
        with patch("split_engine.os.rename", side_effect=rename):
            result = self.run_split()
        self.assertEqual(result.status, "publish_failed")
        self.assertFalse(self.destination.exists())

    def test_final_source_guard_blocks_writes_and_releases(self):
        real_publish = engine.publish_directory
        def publish(staging, destination):
            with self.assertRaises(PermissionError):
                with self.source.open("wb") as stream:
                    stream.write(b"must never write")
            return real_publish(staging, destination)
        with patch.object(engine, "publish_directory", side_effect=publish):
            result = self.run_split()
        self.assertEqual(result.status, "auto_success", result)
        with self.source.open("r+b"):
            pass

    def test_open_writer_blocks_publication(self):
        with self.source.open("r+b"):
            result = self.run_split()
        self.assertEqual(result.status, "io_failed")
        self.assertFalse(self.destination.exists())

    def test_postcommit_private_cleanup_warning_keeps_verified_success(self):
        real_remove = Path.rmdir
        def remove(path):
            if path.name.startswith("atm_split_pending_"):
                real_remove(path)
                raise PermissionError("Injected private-wrapper cleanup warning")
            return real_remove(path)
        with patch.object(Path, "rmdir", new=remove):
            result = self.run_split()
        self.assertEqual(result.status, "auto_success", result)
        self.assertTrue(self.destination.exists())
        self.assertIn("cleanup_warning", result.details)

    def test_failed_staging_cleanup_keeps_reinput_marker(self):
        real_remove = shutil.rmtree
        def remove(path, *args, **kwargs):
            path = Path(path)
            if path.name.startswith("atm_split_pending_"):
                (path / ".atm_split_staging").unlink()
                raise PermissionError("Injected payload cleanup failure")
            if path.name == "ready" and path.parent.name.startswith("atm_split_pending_"):
                raise PermissionError("Injected payload cleanup failure")
            return real_remove(path, *args, **kwargs)
        with patch("split_engine.shutil.rmtree", side_effect=remove), patch.object(
                engine, "publication_source_guard", side_effect=engine.SplitFailure("io_failed", "Source lock failed")):
            result = self.run_split()
        self.assertEqual(result.status, "io_failed")
        self.assertFalse(self.destination.exists())
        remaining = Path(result.details["staging_path"])
        self.assertTrue(engine.is_generated_directory(remaining))
        self.assertEqual(engine.collect_input_files([self.root]), [str(self.source)])

    def test_cleanup_refuses_unmarked_directory(self):
        folder = self.root / "atm_split_regular"
        folder.mkdir()
        important = folder / "keep.txt"
        important.write_text("keep")
        with self.assertRaises(OSError):
            engine._cleanup_staging(folder, self.root)
        self.assertEqual(important.read_text(), "keep")

    def test_cleanup_failure_does_not_mask_cli_failure(self):
        with patch.object(engine, "_cleanup_staging", side_effect=PermissionError("Cleanup blocked")):
            result = self.run_split(MockBackend(fail_at=1))
        self.assertEqual(result.status, "save_failed")
        self.assertEqual(result.details["stderr"], "mock stderr")
        self.assertIn("cleanup_warning", result.details)
        self.assertFalse(self.destination.exists())

    def test_readonly_source_copies_clean_without_changing_source(self):
        import stat
        self.source.chmod(stat.S_IREAD)
        try:
            result = self.run_split()
            self.assertEqual(result.status, "auto_success", result)
            self.assertFalse(self.source.stat().st_mode & stat.S_IWRITE)
        finally:
            self.source.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_unsupported_snapshot(self):
        backend = MockBackend()
        backend.original["slices"] = 1
        result = self.run_split(backend)
        self.assertEqual(result.status, "unsupported_structure")
        self.assertEqual(backend.calls, [])

    def test_no_tags_no_save(self):
        backend = MockBackend()
        backend.original["tags"] = []
        result = self.run_split(backend)
        self.assertEqual(result.status, "no_tags")
        self.assertEqual(backend.calls, [])

    def test_invalid_manual_boundary_no_save(self):
        backend = MockBackend()
        result = self.run_split(backend, mode="manual", boundaries=[6])
        self.assertEqual(result.status, "invalid_boundary")
        self.assertEqual(backend.calls, [])

    def test_source_changed_before_publication(self):
        backend = MockBackend()
        original_extract = backend.extract
        def extract(*args):
            self.source.write_bytes(b"changed externally - mock input only")
            return original_extract(*args)
        backend.extract = extract
        result = self.run_split(backend)
        self.assertEqual(result.status, "source_changed")
        self.assertFalse(result.original_unchanged)
        self.assertFalse(self.destination.exists())
        self.before = engine.sha256(self.source)

    def test_timeout_retains_stdout_stderr_and_command(self):
        backend = engine.AsepriteBackend("mock-aseprite")
        timeout = subprocess.TimeoutExpired(["mock"], 1, output=b"partial stdout", stderr=b"partial stderr")
        with patch("split_engine.subprocess.run", side_effect=timeout):
            with self.assertRaises(engine.SplitFailure) as caught:
                backend._run(self.source, self.root, "extract", "save_failed", output=self.destination)
        self.assertEqual(caught.exception.status, "save_failed")
        self.assertEqual(caught.exception.details["stdout"], "partial stdout")
        self.assertEqual(caught.exception.details["stderr"], "partial stderr")
        self.assertIn(str(self.source), caught.exception.details["command"])

    def test_nonzero_cli_error_retains_diagnostics(self):
        backend = engine.AsepriteBackend("mock-aseprite")
        result = subprocess.CompletedProcess([], 9, b"save stdout", b"save stderr")
        with patch("split_engine.subprocess.run", return_value=result):
            with self.assertRaises(engine.SplitFailure) as caught:
                backend._run(self.source, self.root, "extract", "save_failed", output=self.destination)
        self.assertEqual(caught.exception.details["returncode"], 9)
        self.assertEqual(caught.exception.details["stderr"], "save stderr")

    def test_real_subprocess_timeout_reaps_child(self):
        script = self.root / "sleep.py"
        script.write_text("import time\nprint('child started', flush=True)\ntime.sleep(30)\n", encoding="ascii")
        processes = []
        real_popen = subprocess.Popen
        def popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        backend = engine.AsepriteBackend(sys.executable, timeout=0.3)
        with patch("split_engine.subprocess.Popen", side_effect=popen):
            with self.assertRaises(engine.SplitFailure) as caught:
                backend._run(script, self.root, "extract", "save_failed")
        self.assertEqual(caught.exception.status, "save_failed")
        self.assertTrue(processes)
        self.assertTrue(all(p.poll() is not None for p in processes))


def integration(executable, sample):
    sample = Path(sample).resolve()
    before = engine.sha256(sample)
    root = Path(tempfile.mkdtemp(prefix="atm_split_integration_"))
    source = root / sample.name
    shutil.copy2(sample, source)
    results = []
    for mode in ("auto", "manual"):
        result = engine.split_file(source, executable, mode=mode,
                                   boundaries=[30] if mode == "manual" else None,
                                   output_dir=root / (mode + "_split"), max_size=TEST_MAX_FILE_SIZE)
        if result.status != mode + "_success":
            raise AssertionError(result)
        assert len(result.outputs) == 2
        assert [o["frame_range"] for o in result.outputs] == [[1, 190], [191, 376]]
        assert all(o["bytes"] <= TEST_MAX_FILE_SIZE for o in result.outputs)
        assert all(all(o["verification"].values()) for o in result.outputs)
        assert result.original_unchanged and engine.sha256(source) == before
        results.append(dict(mode=mode, outputs=result.outputs))
    assert engine.sha256(sample) == before
    report = dict(temp_folder=str(root), original=str(sample), original_sha256_before=before,
                  original_sha256_after=engine.sha256(sample), results=results)
    (root / "integration_result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--integration", action="store_true")
    parser.add_argument("--aseprite", default=r"C:\Program Files (x86)\Steam\steamapps\common\Aseprite\aseprite.exe")
    parser.add_argument("--sample", default=str(Path(__file__).resolve().parent / "test_assets" / "Bedivere_Phase1.aseprite"))
    args, unittest_args = parser.parse_known_args()
    result = unittest.main(argv=[__file__, *unittest_args], exit=False)
    if not result.result.wasSuccessful():
        raise SystemExit(1)
    if args.integration:
        integration(args.aseprite, args.sample)
