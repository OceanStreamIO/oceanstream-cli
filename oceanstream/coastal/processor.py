"""CoastalProcessor orchestration — landed as Phase 1+ modules come in.

Mirrors the shape of :class:`oceanstream.echodata.EchodataProcessor` but for
optical retrievals. Pipeline stages:

    1. Load Scene rasters (ACOLITE-corrected).
    2. Build masks (water, deep water, cloud) — Phase 1.3.
    3. Fit scene-mean IOPs via QAA v6 — Phase 1.2.
    4. Fit empirical two-way k(λ) if a deep-water reference is available — Phase 1.5.
    5. Invert closed-form Lee to rho_b + SDB — Phase 1.1 + 1.3.
    6. Run QC gates (pure-water floor, Lyzenga ratio, AC uncertainty) — Phase 1.6 + 3.3.
    7. Emit detectability products (z_max, seabed PAR) — Phase 3.
    8. Emit STAC item + COGs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oceanstream.coastal.aoi import AOI
    from oceanstream.coastal.config import RetrievalConfig
    from oceanstream.coastal.scene import Scene


@dataclass
class CoastalResult:
    """Result from a single-scene coastal retrieval."""

    campaign_id: str
    aoi_name: str
    scene_date: datetime | None = None
    output_dir: Path | None = None
    products: dict[str, Path] = field(default_factory=dict)
    qc_verdict: dict[str, Any] = field(default_factory=dict)
    success: bool = False
    message: str = ""


class CoastalProcessor:
    """High-level orchestrator for one AOI + one Scene — Phase 1+ scaffold."""

    def __init__(
        self,
        config: RetrievalConfig | None = None,
        campaign_id: str | None = None,
        verbose: bool = False,
    ) -> None:
        # Import here to avoid circular imports
        from oceanstream.coastal.config import RetrievalConfig

        self.config = config or RetrievalConfig()
        self.campaign_id = campaign_id or "coastal"
        self.verbose = verbose

    def run(
        self,
        aoi: AOI,
        scene: Scene,
        output_dir: Path,
    ) -> CoastalResult:
        """Run the full retrieval pipeline — Phase 1+."""
        raise NotImplementedError(
            "CoastalProcessor.run lands progressively across Phases 1.1–3. "
            "The pipeline is being ported one stage at a time from "
            "kelp_observe/tools/lee_demo/run_demo.py."
        )
