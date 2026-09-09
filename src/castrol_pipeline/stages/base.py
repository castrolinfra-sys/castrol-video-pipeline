"""The Stage protocol and the job DAG.

Every stage implements one protocol so a vendor swap is a config change.

Two rules that keep the orchestrator honest, and that stages must not break:
  * Stages do NOT write to `jobs`. The orchestrator does.
  * Stages do NOT decide retries. The orchestrator does.

A stage's only jobs are: declare an input_hash over exactly the things that
would change its output, and run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class PipelineStage(StrEnum):
    """Mirrors the `pipeline_stage` Postgres enum. Order matters for display."""

    PREP = "prep"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    REPAIR = "repair"
    COMPOSITE = "composite"
    CHECKS = "checks"
    PUBLISH = "publish"
    DELIVER = "deliver"


class AssetKind(StrEnum):
    """Mirrors the `asset_kind` Postgres enum."""

    SOURCE_PHOTO = "source_photo"
    PLATE = "plate"
    AUDIO = "audio"
    IMAGE_EDIT = "image_edit"
    VIDEO_RAW = "video_raw"
    VIDEO_REPAIR = "video_repair"
    CARD = "card"
    VIDEO_FINAL = "video_final"


#: The DAG. A stage is ready when every stage it depends on has a `succeeded`
#: run. Readiness is COMPUTED, never stored — `jobs.current_stage` is a display
#: convenience for the panel and is never used for routing.
STAGE_DEPENDENCIES: dict[PipelineStage, tuple[PipelineStage, ...]] = {
    PipelineStage.PREP: (),
    PipelineStage.AUDIO: (PipelineStage.PREP,),
    PipelineStage.IMAGE: (PipelineStage.PREP,),
    PipelineStage.VIDEO: (PipelineStage.AUDIO, PipelineStage.IMAGE),
    PipelineStage.REPAIR: (PipelineStage.VIDEO,),
    PipelineStage.COMPOSITE: (PipelineStage.VIDEO,),
    PipelineStage.CHECKS: (PipelineStage.COMPOSITE,),
    PipelineStage.PUBLISH: (PipelineStage.CHECKS,),
    PipelineStage.DELIVER: (PipelineStage.PUBLISH,),
}

#: `repair` is conditional and stays out of the default plan until spike 0.1
#: sets a lipsync threshold. It is wired, not enabled.
CONDITIONAL_STAGES: frozenset[PipelineStage] = frozenset({PipelineStage.REPAIR})

#: The stages a job must complete to be delivered, in dependency order.
DEFAULT_PLAN: tuple[PipelineStage, ...] = (
    PipelineStage.PREP,
    PipelineStage.AUDIO,
    PipelineStage.IMAGE,
    PipelineStage.VIDEO,
    PipelineStage.COMPOSITE,
    PipelineStage.CHECKS,
    PipelineStage.PUBLISH,
    PipelineStage.DELIVER,
)


@dataclass
class JobContext:
    """Everything a stage is allowed to see about a job."""

    job_id: str
    submission_id: str
    script_version: str
    voice_id: str
    plate_id: str | None = None

    # Submission fields the stages need. Populated by the orchestrator.
    user_name: str = ""
    workshop_name: str = ""
    #: What the voiceover says. Already resolved - stages do not re-derive it.
    spoken_place: str = ""
    phone_e164: str = ""
    uniform_id: str = ""
    background_id: str = ""
    image_url_raw: str = ""

    #: Outputs of already-succeeded stages, keyed by stage name:
    #: {"audio": {"output_key": ..., "sha256": ..., "duration_ms": ...}}
    upstream: dict[str, dict[str, Any]] = field(default_factory=dict)

    #: The raw export row, for the prep hash.
    raw: dict[str, Any] = field(default_factory=dict)

    def upstream_sha(self, stage: PipelineStage) -> str:
        try:
            return str(self.upstream[str(stage)]["sha256"])
        except KeyError:
            raise KeyError(
                f"Stage {stage} output is not available on this context — "
                "the orchestrator should not have scheduled this stage yet."
            ) from None

    def upstream_key(self, stage: PipelineStage) -> str:
        return str(self.upstream[str(stage)]["output_key"])


@dataclass(frozen=True)
class StageResult:
    """What a stage hands back. The orchestrator persists it."""

    output_key: str | None = None
    sha256: str | None = None
    asset_kind: AssetKind | None = None
    bytes: int | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    vendor: str | None = None
    model_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    #: Public URL, set only by `publish` and only for the delivered video.
    #: Working artefacts stay private and leave this None.
    cdn_url: str | None = None

    #: What this attempt cost. Set by whichever call reserved budget — for an
    #: async stage that is the SUBMIT, so the figure travels on the
    #: AsyncSubmission and the poller does not re-count it.
    cost_usd: Decimal | None = None
    billed_seconds: Decimal | None = None
    billed_units: Decimal | None = None


@dataclass(frozen=True)
class AsyncSubmission:
    """Stages B and C submit and release. The poller reconciles.

    Both paid remote stages are async, which is not only about worker
    throughput: a submitted run sits in `running`, and the stuck-claim reaper
    only touches `claimed`. A synchronous paid stage that outlives
    STAGE_CLAIM_TIMEOUT_S would be reaped and re-run while the first call was
    still in flight, and billed twice.
    """

    vendor: str
    vendor_task_id: str
    model_id: str
    params: dict[str, Any] = field(default_factory=dict)

    #: Reserved at submit time, recorded on the run immediately. Spend is
    #: incurred by the submit, so it must be visible before the poll — a task
    #: that never completes still cost money.
    cost_usd: Decimal | None = None
    billed_seconds: Decimal | None = None
    billed_units: Decimal | None = None


@runtime_checkable
class Stage(Protocol):
    name: PipelineStage
    is_async: bool

    def input_hash(self, ctx: JobContext) -> str: ...

    def run(self, ctx: JobContext) -> StageResult | AsyncSubmission: ...

    def poll(self, vendor_task_id: str, ctx: JobContext) -> StageResult | None:
        """Async stages only. None means still in flight."""
        ...
