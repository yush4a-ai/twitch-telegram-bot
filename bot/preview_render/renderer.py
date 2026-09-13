from __future__ import annotations

import asyncio
import math
import os
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

from .input import (
    SelectionValidationError,
    SnapshotBecameInvalid,
    SnapshotFileMissing,
    SnapshotValidationError,
    WindowInput,
    plan_window_inputs,
    validate_selection,
    validate_snapshot,
    write_window_manifest,
)
from .models import RenderCapability, RenderConfig, RenderResult, RenderStatus
from .probe import (
    OutputProbeError,
    ProbeTimeoutError,
    SourceProbeError,
    probe_capability,
    probe_output,
    probe_source,
    resolve_common_fps,
    validate_output_probe,
)
from .process import (
    RenderOutputTooLarge,
    RenderProcessExecutor,
    RenderProcessFailure,
    RenderProcessTimeout,
    RenderSnapshotInvalidated,
)
from .storage import RenderJob, RenderStorage, RenderedPreview


def _number(value: float) -> str:
    return format(value, ".15g")


def build_filtergraph(
    plans: Sequence[WindowInput],
    source_fps: Fraction,
    config: RenderConfig,
) -> str:
    if not plans:
        raise ValueError("at least one render input is required")
    branches: list[str] = []
    labels: list[str] = []
    for index, plan in enumerate(plans):
        output_label = f"v{index}"
        labels.append(f"[{output_label}]")
        branches.append(
            f"[{index}:v:0]"
            "settb=AVTB,setpts=PTS-STARTPTS,"
            f"trim=start={_number(plan.local_start_seconds)}:"
            f"duration={_number(plan.duration_seconds)},"
            "setpts=PTS-STARTPTS,"
            f"scale={config.width}:{config.height}:"
            "force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={config.width}:{config.height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,format=yuv420p[{output_label}]"
        )
    if len(plans) == 1:
        common_input = labels[0]
    else:
        branches.append(
            "".join(labels) + f"concat=n={len(plans)}:v=1:a=0[joined]"
        )
        common_input = "[joined]"
    if source_fps > 60:
        branches.append(f"{common_input}fps=60[outv]")
    elif len(plans) == 1:
        branches[-1] = branches[-1][:-4] + "[outv]"
    else:
        branches[-1] = branches[-1][:-8] + "[outv]"
    return ";".join(branches)


def build_render_argv(
    ffmpeg_executable: str,
    manifests: Sequence[Path],
    plans: Sequence[WindowInput],
    source_fps: Fraction,
    output_path: Path,
    config: RenderConfig,
) -> tuple[str, ...]:
    if len(manifests) != len(plans) or not manifests:
        raise ValueError("render inputs do not match plans")
    command: list[str] = [
        ffmpeg_executable,
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "warning",
        "-y",
    ]
    for manifest in manifests:
        command.extend(
            (
                "-f",
                "concat",
                "-safe",
                "0",
                "-protocol_whitelist",
                "file",
                "-i",
                str(manifest),
            )
        )
    command.extend(
        (
            "-filter_complex",
            build_filtergraph(plans, source_fps, config),
            "-map",
            "[outv]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-maxrate",
            "6M",
            "-bufsize",
            "12M",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(output_path),
        )
    )
    return tuple(command)


class PreviewRenderer:
    def __init__(
        self,
        ffmpeg_executable: str,
        ffprobe_executable: str,
        *,
        config: RenderConfig,
        storage: RenderStorage,
        runner: Any,
    ) -> None:
        self._ffmpeg_executable = ffmpeg_executable
        self._ffprobe_executable = ffprobe_executable
        self._config = config
        self._storage = storage
        self._runner = runner
        self._executor = RenderProcessExecutor(
            runner=runner, poll_seconds=config.output_poll_seconds
        )
        self._capability: RenderCapability | None = None

    @classmethod
    def create(
        cls,
        ffmpeg_executable: str | None = None,
        ffprobe_executable: str | None = None,
        *,
        config: RenderConfig | None = None,
        temp_root: Path | None = None,
        runner: Any | None = None,
    ) -> PreviewRenderer:
        from .process import RenderProcessRunner

        resolved_config = config or RenderConfig()
        resolved_runner = runner or RenderProcessRunner()
        return cls(
            ffmpeg_executable or "ffmpeg",
            ffprobe_executable or "ffprobe",
            config=resolved_config,
            storage=RenderStorage(root=temp_root),
            runner=resolved_runner,
        )

    @property
    def active_process_count(self) -> int:
        return self._executor.active_process_count

    @property
    def background_task_count(self) -> int:
        return self._executor.background_task_count

    async def capability(self, refresh: bool = False) -> RenderCapability:
        if self._capability is None or refresh:
            self._capability = await probe_capability(
                self._ffmpeg_executable,
                self._ffprobe_executable,
                runner=self._runner,
                timeout=self._config.capability_timeout,
            )
        return self._capability

    @staticmethod
    def _snapshot_is_valid(snapshot: Any) -> bool:
        try:
            snapshot.ensure_valid()
            return True
        except Exception:
            return False

    @staticmethod
    def _snapshot_files_present(snapshot: Any) -> bool:
        try:
            return all(record.path.is_file() for record in snapshot.records)
        except Exception:
            return False

    async def render(self, snapshot: Any, selection: Any) -> RenderResult:
        try:
            return await asyncio.wait_for(
                self._render(snapshot, selection),
                timeout=self._config.overall_timeout,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return RenderResult(RenderStatus.TIMEOUT, diagnostic_code="render_timeout")

    async def _render(self, snapshot: Any, selection: Any) -> RenderResult:
        job: RenderJob | None = None
        keep_job = False
        try:
            try:
                snapshot_duration = float(snapshot.actual_duration_seconds)
            except (AttributeError, TypeError, ValueError, OverflowError):
                snapshot_duration = math.nan
            validated_selection = validate_selection(selection, snapshot_duration)
            validated_snapshot = validate_snapshot(snapshot)
            plans = plan_window_inputs(validated_snapshot, validated_selection)

            available = await self.capability()
            if not available.available:
                diagnostic = (
                    "ffmpeg_missing"
                    if available.reason is not None
                    and available.reason.value == "missing_executable"
                    else "capability_unavailable"
                )
                return RenderResult(
                    RenderStatus.CAPABILITY_UNAVAILABLE,
                    diagnostic_code=diagnostic,
                )

            if not self._snapshot_is_valid(snapshot):
                raise SnapshotBecameInvalid("snapshot invalidated")
            self._storage.cleanup_orphans()
            job = self._storage.create_job()
            if not self._snapshot_is_valid(snapshot):
                raise SnapshotBecameInvalid("snapshot invalidated")

            manifests: list[Path] = []
            source_infos = []
            for index, plan in enumerate(plans):
                if not self._snapshot_is_valid(snapshot):
                    raise SnapshotBecameInvalid("snapshot invalidated")
                try:
                    manifest = write_window_manifest(
                        plan, job.manifest_path(index)
                    )
                except FileNotFoundError as exc:
                    if not self._snapshot_is_valid(snapshot):
                        raise SnapshotBecameInvalid(
                            "snapshot invalidated"
                        ) from exc
                    raise SnapshotFileMissing("source file missing") from exc
                manifests.append(manifest)
                if not self._snapshot_is_valid(snapshot):
                    raise SnapshotBecameInvalid("snapshot invalidated")
                try:
                    source_infos.append(
                        await probe_source(
                            self._ffprobe_executable,
                            manifest,
                            runner=self._runner,
                            timeout=self._config.source_probe_timeout,
                        )
                    )
                except SourceProbeError:
                    if not self._snapshot_is_valid(snapshot):
                        raise SnapshotBecameInvalid("snapshot invalidated")
                    if not self._snapshot_files_present(snapshot):
                        raise SnapshotFileMissing("source file missing")
                    raise
            source_fps = resolve_common_fps(tuple(source_infos))
            if not self._snapshot_is_valid(snapshot):
                raise SnapshotBecameInvalid("snapshot invalidated")
            job.touch()
            argv = build_render_argv(
                self._ffmpeg_executable,
                tuple(manifests),
                plans,
                source_fps,
                job.temporary_path,
                self._config,
            )
            await self._executor.run(
                argv,
                cwd=job.path,
                output_path=job.temporary_path,
                max_output_bytes=self._config.max_output_bytes,
                is_valid=lambda: self._snapshot_is_valid(snapshot),
                timeout=self._config.encode_timeout,
            )
            if not self._snapshot_is_valid(snapshot):
                raise SnapshotBecameInvalid("snapshot invalidated")
            try:
                actual_size = job.temporary_path.stat().st_size
            except FileNotFoundError as exc:
                if not self._snapshot_is_valid(snapshot):
                    raise SnapshotBecameInvalid("snapshot invalidated") from exc
                raise RenderProcessFailure("ffmpeg_exit") from exc
            if actual_size > self._config.max_output_bytes:
                raise RenderOutputTooLarge("render output exceeded limit")
            payload = await probe_output(
                self._ffprobe_executable,
                job.temporary_path,
                runner=self._runner,
                timeout=self._config.output_probe_timeout,
            )
            metadata = validate_output_probe(
                payload,
                expected_duration=validated_selection.duration_seconds,
                source_fps=source_fps,
                config=self._config,
                actual_size=actual_size,
                window_count=len(plans),
            )
            if not self._snapshot_is_valid(snapshot):
                raise SnapshotBecameInvalid("snapshot invalidated")
            os.replace(job.temporary_path, job.final_path)
            if not self._snapshot_is_valid(snapshot):
                raise SnapshotBecameInvalid("snapshot invalidated")
            artifact = RenderedPreview.from_job(job, metadata)
            keep_job = True
            return RenderResult(RenderStatus.SUCCESS, artifact=artifact)
        except SelectionValidationError:
            return RenderResult(
                RenderStatus.INVALID_SELECTION, diagnostic_code="invalid_selection"
            )
        except SnapshotBecameInvalid:
            return RenderResult(
                RenderStatus.SNAPSHOT_INVALIDATED, diagnostic_code="invalid_snapshot"
            )
        except SnapshotFileMissing:
            return RenderResult(
                RenderStatus.PROCESS_FAILED, diagnostic_code="source_file_missing"
            )
        except SnapshotValidationError:
            return RenderResult(
                RenderStatus.PROCESS_FAILED, diagnostic_code="invalid_snapshot"
            )
        except ProbeTimeoutError:
            return RenderResult(RenderStatus.TIMEOUT, diagnostic_code="render_timeout")
        except SourceProbeError as exc:
            status = (
                RenderStatus.VALIDATION_FAILED
                if exc.diagnostic_code
                in {"source_fps_unreliable", "source_unsupported"}
                else RenderStatus.PROCESS_FAILED
            )
            return RenderResult(status, diagnostic_code=exc.diagnostic_code)
        except OutputProbeError:
            return RenderResult(
                RenderStatus.VALIDATION_FAILED, diagnostic_code="invalid_output"
            )
        except RenderSnapshotInvalidated:
            return RenderResult(
                RenderStatus.SNAPSHOT_INVALIDATED, diagnostic_code="invalid_snapshot"
            )
        except RenderOutputTooLarge:
            return RenderResult(
                RenderStatus.OUTPUT_TOO_LARGE, diagnostic_code="output_too_large"
            )
        except RenderProcessTimeout:
            return RenderResult(RenderStatus.TIMEOUT, diagnostic_code="render_timeout")
        except RenderProcessFailure as exc:
            if not self._snapshot_is_valid(snapshot):
                return RenderResult(
                    RenderStatus.SNAPSHOT_INVALIDATED,
                    diagnostic_code="invalid_snapshot",
                )
            if not self._snapshot_files_present(snapshot):
                return RenderResult(
                    RenderStatus.PROCESS_FAILED,
                    diagnostic_code="source_file_missing",
                )
            diagnostic = (
                exc.diagnostic_code
                if exc.diagnostic_code in {"ffmpeg_missing", "ffmpeg_exit"}
                else "ffmpeg_exit"
            )
            return RenderResult(
                RenderStatus.PROCESS_FAILED, diagnostic_code=diagnostic
            )
        except asyncio.CancelledError:
            raise
        except (OSError, PermissionError):
            return RenderResult(
                RenderStatus.INTERNAL_ERROR, diagnostic_code="storage_unavailable"
            )
        except Exception:
            return RenderResult(
                RenderStatus.INTERNAL_ERROR, diagnostic_code="unexpected_error"
            )
        finally:
            if job is not None and not keep_job:
                job.cleanup()
