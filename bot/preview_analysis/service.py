from __future__ import annotations

import asyncio
import math
import os
import stat
from pathlib import Path
from typing import Any

from bot.preview_capture import PinnedSnapshot, SnapshotInvalidated

from .concat import (
    AnalysisTempManager,
    SnapshotBecameInvalid,
    SnapshotValidationError,
    validate_snapshot,
    write_concat_manifest,
)
from .metrics import (
    AudioMetadataParser,
    AudioMetrics,
    MetricParseError,
    VisualMetadataParser,
    VisualMetrics,
    build_analysis_bins,
    read_fingerprints,
)
from .models import (
    AnalysisConfig,
    AnalysisResult,
    AnalysisStatus,
    HighlightSelection,
)
from .process import (
    AnalysisOutputLimitError,
    AnalysisProcessExecutor,
    AnalysisProcessFailure,
    AnalysisProcessTimeout,
    AnalysisSnapshotInvalidated,
)
from .selector import select_highlights


def _concat_input_argv(manifest: Path) -> tuple[str, ...]:
    return (
        "-f",
        "concat",
        "-safe",
        "0",
        "-protocol_whitelist",
        "file",
        "-segment_time_metadata",
        "1",
        "-i",
        str(manifest),
    )


def build_visual_argv(
    executable: str,
    manifest: Path,
    fingerprint_path: Path,
    config: AnalysisConfig,
) -> tuple[str, ...]:
    graph = (
        "[0:v:0]settb=AVTB,setpts=PTS-STARTPTS,"
        f"fps={config.analysis_fps},"
        f"scale={config.analysis_width}:{config.analysis_height}:flags=area,"
        "format=yuv420p,split=2[metrics_in][finger_in];"
        "[metrics_in]signalstats,"
        f"scdet=t={format(config.scene_cut_threshold, 'g')},"
        "blackframe=amount=0:threshold=32,"
        f"freezedetect=n={format(config.freeze_noise_db, 'g')}dB:"
        f"d={format(config.freeze_duration, 'g')},"
        "metadata=print:file=pipe\\\\:1:direct=1[metrics_out];"
        f"[finger_in]fps=1,scale={config.fingerprint_width}:"
        f"{config.fingerprint_height}:flags=area,format=gray[finger_out]"
    )
    return (
        executable,
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "warning",
        "-y",
        *_concat_input_argv(manifest),
        "-filter_complex",
        graph,
        "-map",
        "[metrics_out]",
        "-f",
        "null",
        os.devnull,
        "-map",
        "[finger_out]",
        "-c:v",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        str(fingerprint_path),
    )


def build_audio_argv(
    executable: str,
    manifest: Path,
) -> tuple[str, ...]:
    audio_filter = (
        "aresample=48000,asetpts=PTS-STARTPTS,asetnsamples=n=48000:p=1,"
        "astats=metadata=1:reset=1:measure_perchannel=none:"
        "measure_overall=RMS_level+Peak_level,"
        "ametadata=print:file=pipe\\\\:1:direct=1"
    )
    return (
        executable,
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "warning",
        *_concat_input_argv(manifest),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "copy",
        "-af",
        audio_filter,
        "-f",
        "null",
        os.devnull,
    )


def _empty_result(
    status: AnalysisStatus, diagnostic_code: str | None = None
) -> AnalysisResult:
    return AnalysisResult(status, HighlightSelection(), diagnostic_code)


def _fingerprint_within_limit(path: Path, maximum_bytes: int) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and not path.is_symlink()
        and info.st_size <= maximum_bytes
    )


class HighlightAnalyzer:
    def __init__(
        self,
        ffmpeg_executable: str = "ffmpeg",
        *,
        config: AnalysisConfig | None = None,
        temp_root: Path | None = None,
        executor: AnalysisProcessExecutor | None = None,
    ) -> None:
        self._ffmpeg_executable = ffmpeg_executable
        self._config = config or AnalysisConfig()
        self._temp_manager = AnalysisTempManager(temp_root)
        self._executor = executor or AnalysisProcessExecutor(
            line_max_bytes=self._config.metadata_line_max_bytes,
            total_max_bytes=self._config.metadata_total_max_bytes,
        )

    async def analyze(self, snapshot: PinnedSnapshot) -> AnalysisResult:
        try:
            return await asyncio.wait_for(
                self._analyze(snapshot), self._config.overall_timeout
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return _empty_result(
                AnalysisStatus.TIMEOUT, "analysis_timeout"
            )
        except (SnapshotBecameInvalid, SnapshotInvalidated, AnalysisSnapshotInvalidated):
            return _empty_result(
                AnalysisStatus.SNAPSHOT_INVALIDATED, "invalid_snapshot"
            )
        except SnapshotValidationError:
            if not bool(getattr(snapshot, "valid", False)):
                return _empty_result(
                    AnalysisStatus.SNAPSHOT_INVALIDATED, "invalid_snapshot"
                )
            diagnostic = "empty_snapshot" if not tuple(snapshot.records) else "missing_segment"
            return _empty_result(AnalysisStatus.PROCESS_FAILED, diagnostic)
        except FileNotFoundError:
            if not bool(getattr(snapshot, "valid", False)):
                return _empty_result(
                    AnalysisStatus.SNAPSHOT_INVALIDATED, "invalid_snapshot"
                )
            return _empty_result(AnalysisStatus.PROCESS_FAILED, "missing_segment")
        except AnalysisProcessTimeout:
            return _empty_result(AnalysisStatus.TIMEOUT, "analysis_timeout")
        except AnalysisProcessFailure as exc:
            diagnostic = (
                exc.diagnostic_code
                if exc.diagnostic_code in {"ffmpeg_missing", "ffmpeg_exit"}
                else "ffmpeg_exit"
            )
            return _empty_result(AnalysisStatus.PROCESS_FAILED, diagnostic)
        except AnalysisOutputLimitError:
            return _empty_result(AnalysisStatus.MALFORMED_METRICS, "output_limit")
        except MetricParseError:
            return _empty_result(
                AnalysisStatus.MALFORMED_METRICS, "invalid_metadata"
            )
        except Exception:
            return _empty_result(AnalysisStatus.INTERNAL_ERROR, "unexpected_error")

    async def _analyze(self, snapshot: PinnedSnapshot) -> AnalysisResult:
        validated = validate_snapshot(snapshot)
        duration = validated.duration_seconds
        if duration < self._config.min_snapshot_seconds:
            return _empty_result(AnalysisStatus.SNAPSHOT_TOO_SHORT)

        job = self._temp_manager.create_job()
        try:
            snapshot.ensure_valid()
            manifest = write_concat_manifest(validated, job)

            snapshot.ensure_valid()
            visual_parser = VisualMetadataParser(
                max_records=math.ceil(duration * self._config.analysis_fps)
                + self._config.visual_record_tolerance
            )
            visual_run = await self._executor.run(
                build_visual_argv(
                    self._ffmpeg_executable,
                    manifest,
                    job.fingerprint_path,
                    self._config,
                ),
                cwd=job.path,
                parser=visual_parser,
                is_valid=lambda: bool(snapshot.valid),
                timeout=self._config.pass_timeout,
                output_guard=lambda: _fingerprint_within_limit(
                    job.fingerprint_path,
                    math.ceil(duration)
                    * self._config.fingerprint_frame_bytes
                    + self._config.fingerprint_size_tolerance,
                ),
            )
            snapshot.ensure_valid()
            if not isinstance(visual_run.parsed, VisualMetrics):
                raise MetricParseError("visual parser returned unexpected data")
            fingerprints = read_fingerprints(
                job.fingerprint_path, duration, self._config
            )

            snapshot.ensure_valid()
            audio_parser = AudioMetadataParser(
                max_records=math.ceil(duration)
                + self._config.audio_record_tolerance
            )
            audio_run = await self._executor.run(
                build_audio_argv(self._ffmpeg_executable, manifest),
                cwd=job.path,
                parser=audio_parser,
                is_valid=lambda: bool(snapshot.valid),
                timeout=self._config.pass_timeout,
            )
            snapshot.ensure_valid()
            if not isinstance(audio_run.parsed, AudioMetrics):
                raise MetricParseError("audio parser returned unexpected data")

            bins = build_analysis_bins(
                duration,
                visual_run.parsed,
                audio_run.parsed,
                fingerprints,
                self._config,
            )
            selection = select_highlights(bins, duration, self._config)
            snapshot.ensure_valid()
            if selection.windows:
                return AnalysisResult(AnalysisStatus.SUCCESS, selection)
            return _empty_result(AnalysisStatus.NO_SELECTION)
        finally:
            job.cleanup()
