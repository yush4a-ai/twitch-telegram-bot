from __future__ import annotations

import math
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bot import preview_capture as capture


def _write_ts(path: Path, packets: int = 2) -> None:
    packet = bytes([0x47]) + (b"\x00" * 187)
    path.write_bytes(packet * packets)


def _write_manifest(path: Path, media_sequence: int, entries: list[tuple[float, str]]) -> None:
    lines = ["#EXTM3U", f"#EXT-X-MEDIA-SEQUENCE:{media_sequence}"]
    for duration, uri in entries:
        lines.extend((f"#EXTINF:{duration},", uri))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class HlsArgvTests(unittest.TestCase):
    def test_builds_fixed_copy_only_hls_argv(self) -> None:
        with TemporaryDirectory() as temp_dir:
            owned = Path(temp_dir)
            source = capture.UrlCaptureInput(
                "https://video.example/live.m3u8?token=secret"
            )

            argv = capture.build_hls_argv("ffmpeg", source, owned)

        self.assertEqual(
            argv,
            (
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-i",
                source.url,
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-sn",
                "-dn",
                "-c",
                "copy",
                "-f",
                "hls",
                "-hls_segment_type",
                "mpegts",
                "-hls_time",
                "2",
                "-hls_list_size",
                "128",
                "-hls_flags",
                "temp_file",
                "-hls_segment_filename",
                str(owned / "segment-%09d.ts"),
                str(owned / "index.m3u8"),
            ),
        )
        self.assertNotIn("-r", argv)
        self.assertNotIn("scale", " ".join(argv))

    def test_file_input_is_one_argv_element(self) -> None:
        source = capture.FileCaptureInput("fixture with spaces.ts")
        argv = capture.build_hls_argv("ffmpeg", source, Path("owned"))

        input_index = argv.index("-i") + 1
        self.assertEqual(argv[input_index], "fixture with spaces.ts")


class ManifestWatcherTests(unittest.TestCase):
    def test_registers_only_complete_manifest_entries_with_sequence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            owned = Path(temp_dir)
            _write_ts(owned / "segment-000000005.ts")
            _write_manifest(
                owned / "index.m3u8", 41, [(2.25, "segment-000000005.ts")]
            )
            watcher = capture.ManifestWatcher(owned, monotonic=lambda: 12.5)

            result = watcher.scan()

        self.assertEqual(result.invalid_entries, 0)
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.segments[0].sequence, 41)
        self.assertEqual(result.segments[0].duration_seconds, 2.25)
        self.assertEqual(result.segments[0].completed_at_monotonic, 12.5)

    def test_tmp_and_incomplete_entries_are_never_usable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            owned = Path(temp_dir)
            _write_ts(owned / "segment-000000000.ts.tmp")
            (owned / "index.m3u8.tmp").write_text(
                "#EXTM3U\n#EXTINF:2,\nsegment-000000000.ts.tmp\n",
                encoding="utf-8",
            )
            watcher = capture.ManifestWatcher(owned)

            missing_final = watcher.scan()
            (owned / "index.m3u8").write_text(
                "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:2,\n",
                encoding="utf-8",
            )
            incomplete = watcher.scan()

        self.assertEqual(missing_final.segments, ())
        self.assertEqual(incomplete.segments, ())

    def test_rejects_non_owned_uri_symlink_zero_and_invalid_ts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owned = root / "owned"
            owned.mkdir()
            _write_ts(root / "outside.ts")
            (owned / "segment-000000001.ts").write_bytes(b"")
            (owned / "segment-000000002.ts").write_bytes(b"not-mpeg-ts")
            entries = [
                (2.0, "../outside.ts"),
                (2.0, "segment-000000001.ts"),
                (2.0, "segment-000000002.ts"),
            ]
            if hasattr(os, "symlink"):
                try:
                    os.symlink(
                        root / "outside.ts", owned / "segment-000000003.ts"
                    )
                except OSError:
                    pass
                else:
                    entries.append((2.0, "segment-000000003.ts"))
            _write_manifest(owned / "index.m3u8", 0, entries)

            result = capture.ManifestWatcher(owned).scan()

        self.assertEqual(result.segments, ())
        self.assertEqual(result.invalid_entries, len(entries))

    def test_rejects_nonfinite_nonpositive_duration_and_wrong_basename(self) -> None:
        with TemporaryDirectory() as temp_dir:
            owned = Path(temp_dir)
            for number in range(4):
                _write_ts(owned / f"segment-{number:09d}.ts")
            _write_manifest(
                owned / "index.m3u8",
                0,
                [
                    (0.0, "segment-000000000.ts"),
                    (-1.0, "segment-000000001.ts"),
                    (math.inf, "segment-000000002.ts"),
                    (2.0, "wrong.ts"),
                ],
            )

            result = capture.ManifestWatcher(owned).scan()

        self.assertEqual(result.segments, ())
        self.assertEqual(result.invalid_entries, 4)

    def test_old_evicted_missing_entry_is_ignored_and_next_unseen_registers(self) -> None:
        with TemporaryDirectory() as temp_dir:
            owned = Path(temp_dir)
            first_path = owned / "segment-000000000.ts"
            _write_ts(first_path)
            _write_manifest(
                owned / "index.m3u8", 100, [(2.0, first_path.name)]
            )
            watcher = capture.ManifestWatcher(owned)
            first = watcher.scan()
            first_path.unlink()
            second_path = owned / "segment-000000001.ts"
            _write_ts(second_path)
            _write_manifest(
                owned / "index.m3u8",
                100,
                [(2.0, first_path.name), (2.0, second_path.name)],
            )

            second = watcher.scan()

        self.assertEqual([item.sequence for item in first.segments], [100])
        self.assertEqual([item.sequence for item in second.segments], [101])
        self.assertEqual(second.invalid_entries, 0)
        self.assertEqual(watcher.highest_seen_sequence, 101)
        self.assertFalse(hasattr(watcher, "seen_sequences"))

    def test_same_manifest_never_reregisters_sequence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            owned = Path(temp_dir)
            path = owned / "segment-000000000.ts"
            _write_ts(path)
            _write_manifest(owned / "index.m3u8", 7, [(2.0, path.name)])
            watcher = capture.ManifestWatcher(owned)

            first = watcher.scan()
            second = watcher.scan()

        self.assertEqual(len(first.segments), 1)
        self.assertEqual(second.segments, ())

    def test_buffer_eviction_of_still_listed_entry_does_not_break_next_scan(self) -> None:
        with TemporaryDirectory() as temp_dir:
            owned = Path(temp_dir)
            first_path = owned / "segment-000000000.ts"
            _write_ts(first_path)
            _write_manifest(owned / "index.m3u8", 50, [(2.0, first_path.name)])
            watcher = capture.ManifestWatcher(owned)
            buffer = capture.RollingSegmentBuffer(owned, 2, 188 * 2)
            self.assertTrue(buffer.register(watcher.scan().segments[0]))
            second_path = owned / "segment-000000001.ts"
            _write_ts(second_path)
            _write_manifest(
                owned / "index.m3u8",
                50,
                [(2.0, first_path.name), (2.0, second_path.name)],
            )
            self.assertTrue(buffer.register(watcher.scan().segments[0]))
            self.assertFalse(first_path.exists())
            third_path = owned / "segment-000000002.ts"
            _write_ts(third_path)
            _write_manifest(
                owned / "index.m3u8",
                50,
                [
                    (2.0, first_path.name),
                    (2.0, second_path.name),
                    (2.0, third_path.name),
                ],
            )

            result = watcher.scan()

        self.assertEqual([item.sequence for item in result.segments], [52])
        self.assertEqual(result.invalid_entries, 0)


if __name__ == "__main__":
    unittest.main()
