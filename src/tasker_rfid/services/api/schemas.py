"""Request and response shapes.

These are what the interactive docs page at /docs shows you, so each one
carries a description and an example. Fields mirror the columns in
SPEC.md section 3.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------


class ContentLine(BaseModel):
    """What is inside a container. SPEC.md section 3, container_contents."""

    sku_id: str = Field(examples=["CONE-STD-120"])
    quantity: int = Field(gt=0, description="Number of boxes", examples=[24])
    lot: str | None = Field(default=None, examples=["L-2026-0412"])
    produced_at: date | None = None
    expiry: date | None = None


class NewContainer(BaseModel):
    """Register a physical container against its factory-locked chip serial."""

    tid: str = Field(
        description="The chip's TID. This is the primary key and never changes.",
        examples=["E28011702000A7F312345678"],
    )
    kind: str = Field(default="BOX", description="BOX, PALLET or TOTE", examples=["BOX"])
    epc: str | None = Field(
        default=None,
        description="Informational only. Nothing is ever written to a tag.",
    )
    reusable: bool = Field(
        default=False,
        description="True for pallets and totes, which come back and are used again.",
    )
    parent_tid: str | None = Field(
        default=None, description="TID of the container this one sits on, if any."
    )
    contents: list[ContentLine] = Field(default_factory=list)


class AttachChildren(BaseModel):
    """Build a pallet: attach boxes to it."""

    child_tids: list[str] = Field(
        min_length=1,
        description="TIDs of containers to place on this one.",
        examples=[["E28011702000AAA1", "E28011702000AAA2"]],
    )


class ContainerSummary(BaseModel):
    container_id: int
    tid: str
    kind: str
    status: str
    reusable: bool
    parent_id: int | None = None
    epc: str | None = None
    created_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_portal: str | None = None


class Movement(BaseModel):
    from_status: str | None
    to_status: str
    portal: str | None
    session_id: int | None
    occurred_at: datetime
    source: str


class AnomalySummary(BaseModel):
    id: int
    tid: str | None
    container_id: int | None
    kind: str
    detail: dict | None
    occurred_at: datetime
    resolved: bool
    resolved_by: str | None = None
    resolved_at: datetime | None = None


class ContainerDetail(BaseModel):
    """Everything known about one container. SPEC.md section 6: full history."""

    container: ContainerSummary
    contents: list[dict]
    children: list[ContainerSummary]
    movements: list[Movement]
    anomalies: list[AnomalySummary]


# ---------------------------------------------------------------------------
# Stock and reports
# ---------------------------------------------------------------------------


class StockLine(BaseModel):
    sku_id: str
    name: str
    boxes: int


class StockHolder(BaseModel):
    """A container currently holding some of a SKU."""

    tid: str
    container_id: int
    kind: str
    quantity: int
    lot: str | None = None
    expiry: date | None = None
    last_seen_at: datetime | None = None
    last_portal: str | None = None


class ConsumptionLine(BaseModel):
    customer: str
    sku: str
    boxes: int


# ---------------------------------------------------------------------------
# Dispatch sessions
# ---------------------------------------------------------------------------


class NewDispatchSession(BaseModel):
    """Open the dock for a customer before loading starts."""

    customer_id: str = Field(examples=["CUST-0001"])
    order_ref: str | None = Field(default=None, examples=["SO-99312"])
    operator: str | None = Field(default=None, examples=["mlopez"])


class DispatchSession(BaseModel):
    session_id: int
    customer_id: str | None
    order_ref: str | None
    operator: str | None
    opened_at: datetime
    closed_at: datetime | None = None


class DispatchSessionDetail(BaseModel):
    """What actually went out during a session."""

    session: DispatchSession
    containers: list[dict]
    totals_by_sku: list[dict]


# ---------------------------------------------------------------------------
# Cycle counts
# ---------------------------------------------------------------------------


class NewCycleCount(BaseModel):
    operator: str | None = Field(default=None, examples=["mlopez"])
    notes: str | None = None


class CycleScan(BaseModel):
    tid: str = Field(examples=["E28011702000A7F312345678"])


class VarianceReport(BaseModel):
    """What the floor holds versus what the system believes."""

    cycle_id: int
    expected: int = Field(description="Containers the system believes are IN_STOCK")
    found: int = Field(description="Of those, how many were scanned")
    missing: list[dict] = Field(description="IN_STOCK but not scanned")
    unexpected: list[dict] = Field(description="Scanned but not expected in stock")
    anomalies_raised: int


# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------


class ResolveAnomaly(BaseModel):
    resolved_by: str = Field(examples=["mlopez"])
    note: str | None = Field(
        default=None, description="What was done about it.", examples=["Recounted; tag replaced."]
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class ReaderHealth(BaseModel):
    reader_id: str
    last_read_at: datetime | None
    minutes_since_last_read: float | None
    reads_today: int
    healthy: bool


class QueueDepth(BaseModel):
    reads_awaiting_debounce: int
    observations_awaiting_state_engine: int


class HealthReport(BaseModel):
    status: str = Field(description="ok, degraded, or unknown")
    database: str
    checked_at: datetime
    within_working_hours: bool
    no_read_alert_hours: int
    readers: list[ReaderHealth]
    queue_depth: QueueDepth
    unresolved_anomalies: int
    warnings: list[str]
