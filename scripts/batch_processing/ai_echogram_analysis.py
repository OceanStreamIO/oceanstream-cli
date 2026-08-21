#!/usr/bin/env python3
"""AI-powered echogram analysis using Azure OpenAI vision models.

Standalone pipeline step that:
  1. Reads echogram PNG files
  2. Sends them to Azure OpenAI (GPT-4o, GPT-5, etc.) for interpretation
  3. Returns structured JSON with biological features + bounding boxes
  4. Renders polygon overlays on the echogram

Adapted from os-webapp/server/services/ai_vision.py for batch pipeline use.

Usage:
    # Analyze a single echogram
    python ai_echogram_analysis.py --image output/2023-08-10/echograms/multifrequency/2023-08-10--short_pulse--denoised-38kHz.png

    # Analyze all echograms in a directory
    python ai_echogram_analysis.py --dir output/2023-08-10/echograms/multifrequency/

    # With context
    python ai_echogram_analysis.py --image echogram.png --frequency 38 --depth-range "10-500m" --context "Tropical Pacific, Saildrone TPOS 2023"

Environment:
    AZURE_OPENAI_ENDPOINT     — Azure OpenAI resource endpoint
    AZURE_OPENAI_API_KEY      — API key
    AZURE_OPENAI_DEPLOYMENT   — Deployment name (default: gpt-4o)
    AZURE_OPENAI_API_VERSION  — API version (default: 2024-12-01-preview)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Analysis prompt (fisheries acoustics domain expert)
# ---------------------------------------------------------------------------

ECHOGRAM_ANALYSIS_PROMPT = """You are an expert fisheries acoustician analyzing an echogram image from a scientific echosounder (EK80 or similar split-beam system). The echogram uses the "ocean_r" colormap with Sv (volume backscattering strength) in dB re 1 m⁻¹, typically ranging from −80 dB (dark/blue, empty water) to −30 dB (bright/warm, strong scatterers). Depth increases downward (y-axis); time or distance increases rightward (x-axis).

Instrument & survey context:
- Frequency: {frequency_khz} kHz
- Depth range: {depth_range}
- Geographic location: {location}
- Survey context: {survey_context}

Frequency-specific interpretation guidelines:
- 38 kHz: deepest penetration (>1000 m), standard for seabed detection and large fish with swimbladders. Strong resonance from swimbladder-bearing species. Fish schools typically −50 to −30 dB Sv.
- 70 kHz: intermediate depth, good for mesopelagic targets.
- 120 kHz: dominant for fish target strength estimation, moderate depth (~500 m).
- 200 kHz: shallow range (~200 m), high resolution, sensitive to zooplankton and euphausiids. Plankton layers typically −75 to −55 dB Sv.
- 333 kHz: shallowest (<100 m), highest resolution, near-surface organisms.

Classification guidance:
- Deep Scattering Layer (DSL): broad diffuse layer typically at 200–800 m during daytime, migrating to 50–200 m at night (diel vertical migration). Often −70 to −55 dB Sv.
- Shallow Scattering Layer (SSL): similar but shallower (50–200 m day, near-surface night).
- Fish schools: discrete, high-Sv aggregations (−50 to −30 dB), often oval/elliptical with sharp boundaries.
- Plankton layers: diffuse, horizontally extended, moderate Sv (−75 to −55 dB).
- Swimbladder resonance: enhanced backscatter at low frequencies (18–38 kHz) from gas-filled swim bladders.
- In open-ocean / deep pelagic settings (Tropical Pacific, etc.), the seabed is typically beyond acoustic range (>2000 m). Report "detected": false with a note that depth exceeds echosounder range.

Analyze this echogram and return a JSON object:

{{
  "summary": "Brief 2-3 sentence scientific summary of what the echogram shows",
  "biological_features": [
    {{
      "type": "fish_school|DSL|SSL|plankton_layer|individual_targets|swimbladder_resonance|krill_aggregation|myctophid_layer",
      "species_guess": "species or taxonomic group, or null if uncertain",
      "confidence": "high|medium|low",
      "bounding_box": {{
        "x_percent": [left, right],
        "y_percent": [top, bottom]
      }},
      "description": "Scientific description including scattering characteristics",
      "estimated_density": "dense|moderate|sparse|null",
      "depth_range_m": [min_depth, max_depth],
      "sv_range_db": [min_sv, max_sv]
    }}
  ],
  "seafloor": {{
    "detected": true,
    "depth_m": null,
    "substrate_type": "hard|soft|rocky|sandy|muddy|null",
    "confidence": "high|medium|low",
    "note": "Optional note, e.g. 'beyond acoustic range' for open-ocean data"
  }},
  "noise_assessment": {{
    "overall_quality": "excellent|good|fair|poor",
    "issues": ["e.g. background noise, impulse noise, attenuation/bubble wash, near-surface interference, ring-down artifact"],
    "regions": [
      {{
        "type": "impulse_noise|background_noise|near_surface_interference|attenuation|ring_down",
        "bounding_box": {{
          "x_percent": [left, right],
          "y_percent": [top, bottom]
        }},
        "severity": "mild|moderate|severe"
      }}
    ]
  }},
  "annotations": [
    {{
      "text": "Label text for overlay",
      "position_percent": {{"x": 50, "y": 50}},
      "style": "label|arrow|region"
    }}
  ],
  "recommendations": ["Actionable recommendations for the scientist, e.g. denoising, frequency comparison, diel migration analysis"]
}}

Rules:
- Bounding box coordinates are percentages (0-100) of the plot area (excluding axes/colorbar)
- x_percent[0] is left edge, x_percent[1] is right edge (time/distance axis)
- y_percent[0] is top edge, y_percent[1] is bottom edge (depth axis, 0=surface)
- Be conservative with species identification — use "unidentified fish school" or taxonomic group
- If the echogram clearly shows open ocean with no seabed return, set seafloor.detected=false
- Note any diel vertical migration patterns if time span covers day-night transition
- For noise_assessment.regions, include bounding boxes for each spatially identifiable noise artifact
- Sv values in dB re 1 m⁻¹"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BoundingBox:
    x_percent: list[float]  # [left, right] as % of plot area
    y_percent: list[float]  # [top, bottom] as % of plot area


@dataclass
class BiologicalFeature:
    type: str
    confidence: str
    bounding_box: Optional[BoundingBox] = None
    species_guess: Optional[str] = None
    description: Optional[str] = None
    estimated_density: Optional[str] = None
    depth_range_m: Optional[list[float]] = None
    sv_range_db: Optional[list[float]] = None


@dataclass
class NoiseRegion:
    type: str
    severity: str = "mild"
    bounding_box: Optional[BoundingBox] = None


@dataclass
class EchogramAnalysis:
    """Structured result from AI echogram analysis."""
    summary: str = ""
    biological_features: list[BiologicalFeature] = field(default_factory=list)
    seafloor: Optional[dict] = None
    noise_assessment: Optional[dict] = None
    annotations: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    model: str = ""
    tokens: Optional[dict] = None
    raw_response: Optional[str] = None


# ---------------------------------------------------------------------------
# Core analysis function (standalone, no web framework dependencies)
# ---------------------------------------------------------------------------


async def analyze_echogram(
    image_path: str | Path,
    *,
    frequency_khz: Optional[int] = None,
    depth_range: Optional[str] = None,
    survey_context: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    deployment: Optional[str] = None,
    endpoint: Optional[str] = None,
    api_key: Optional[str] = None,
    api_version: Optional[str] = None,
) -> EchogramAnalysis:
    """
    Analyze an echogram image using Azure OpenAI vision.

    Parameters
    ----------
    image_path : path to PNG echogram file
    frequency_khz : echosounder frequency (e.g. 38, 200)
    depth_range : depth range string (e.g. "10-500m")
    survey_context : free text context (e.g. "Tropical Pacific, Saildrone")
    latitude, longitude : geographic centre of echogram
    deployment : Azure OpenAI deployment name (default: env AZURE_OPENAI_DEPLOYMENT or "gpt-4o")
    endpoint : Azure OpenAI endpoint (default: env AZURE_OPENAI_ENDPOINT)
    api_key : Azure OpenAI API key (default: env AZURE_OPENAI_API_KEY)
    api_version : API version (default: env AZURE_OPENAI_API_VERSION)

    Returns
    -------
    EchogramAnalysis with structured fields
    """
    from openai import AsyncAzureOpenAI

    # Resolve credentials from env if not provided
    endpoint = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY")
    api_version = api_version or os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    model = deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5")

    if not endpoint or not api_key:
        raise ValueError(
            "Azure OpenAI credentials required. Set AZURE_OPENAI_ENDPOINT and "
            "AZURE_OPENAI_API_KEY environment variables."
        )

    # Read image
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Echogram not found: {image_path}")

    image_bytes = image_path.read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    # Format prompt
    if latitude is not None and longitude is not None:
        location = f"{latitude:.2f}°N, {longitude:.2f}°E"
    else:
        location = "unknown"

    prompt = ECHOGRAM_ANALYSIS_PROMPT.format(
        frequency_khz=frequency_khz or "unknown",
        depth_range=depth_range or "unknown",
        location=location,
        survey_context=survey_context or "No additional context provided",
    )

    # Call Azure OpenAI
    client = AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )

    # Reasoning models don't support temperature/max_tokens
    is_reasoning = model.startswith("gpt-5") or model.startswith("o3") or model.startswith("o4")

    kwargs: dict = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}",
                            "detail": "high",
                        },
                    },
                ],
            },
        ],
        "response_format": {"type": "json_object"},
    }

    if is_reasoning:
        kwargs["max_completion_tokens"] = 16384
        kwargs["reasoning_effort"] = "medium"
    else:
        kwargs["max_tokens"] = 4096
        kwargs["temperature"] = 0.3

    logger.info("Sending echogram to %s (%s)...", model, endpoint.split("//")[1].split(".")[0])
    response = await client.chat.completions.create(**kwargs)

    content = response.choices[0].message.content or ""

    # Parse JSON (handle markdown fences)
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        data = json.loads(cleaned) if cleaned else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse AI response as JSON")
        return EchogramAnalysis(
            raw_response=content,
            model=model,
            tokens={
                "prompt": response.usage.prompt_tokens if response.usage else 0,
                "completion": response.usage.completion_tokens if response.usage else 0,
            },
        )

    # Build structured result
    features = []
    for f in data.get("biological_features", []):
        bb = None
        if f.get("bounding_box"):
            bb = BoundingBox(
                x_percent=f["bounding_box"].get("x_percent", [0, 100]),
                y_percent=f["bounding_box"].get("y_percent", [0, 100]),
            )
        features.append(BiologicalFeature(
            type=f.get("type", "unknown"),
            confidence=f.get("confidence", "low"),
            bounding_box=bb,
            species_guess=f.get("species_guess"),
            description=f.get("description"),
            estimated_density=f.get("estimated_density"),
            depth_range_m=f.get("depth_range_m"),
            sv_range_db=f.get("sv_range_db"),
        ))

    return EchogramAnalysis(
        summary=data.get("summary", ""),
        biological_features=features,
        seafloor=data.get("seafloor"),
        noise_assessment=data.get("noise_assessment"),
        annotations=data.get("annotations", []),
        recommendations=data.get("recommendations", []),
        model=model,
        tokens={
            "prompt": response.usage.prompt_tokens if response.usage else 0,
            "completion": response.usage.completion_tokens if response.usage else 0,
        },
    )


# ---------------------------------------------------------------------------
# Overlay rendering
# ---------------------------------------------------------------------------


def render_overlay(
    image_path: str | Path,
    analysis: EchogramAnalysis,
    output_path: Optional[str | Path] = None,
    *,
    plot_area_bbox: Optional[tuple[float, float, float, float]] = None,
) -> Path:
    """
    Render bounding box polygons and annotations on top of an echogram PNG.

    Parameters
    ----------
    image_path : source echogram PNG
    analysis : EchogramAnalysis result from analyze_echogram()
    output_path : where to save (default: adds '--annotated' suffix)
    plot_area_bbox : (left, top, right, bottom) pixel coords of the plot area
                     within the image. If None, auto-detected by assuming
                     ~12% left margin, ~5% right margin, ~8% top, ~10% bottom.

    Returns
    -------
    Path to the annotated image
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from PIL import Image

    image_path = Path(image_path)
    img = Image.open(image_path)
    img_w, img_h = img.size

    # Estimate plot area if not provided (typical matplotlib echogram layout)
    if plot_area_bbox is None:
        left = int(img_w * 0.10)
        right = int(img_w * 0.92)
        top = int(img_h * 0.06)
        bottom = int(img_h * 0.88)
    else:
        left, top, right, bottom = [int(v) for v in plot_area_bbox]

    plot_w = right - left
    plot_h = bottom - top

    # Create figure at image resolution
    dpi = 100
    fig, ax = plt.subplots(figsize=(img_w / dpi, img_h / dpi), dpi=dpi)
    ax.imshow(img)
    ax.axis("off")
    ax.set_position([0, 0, 1, 1])

    # Color scheme for feature types
    COLORS = {
        "fish_school": "#FF4444",
        "DSL": "#4488FF",
        "SSL": "#44AAFF",
        "plankton_layer": "#44CC44",
        "krill_aggregation": "#FF8844",
        "myctophid_layer": "#AA44FF",
        "individual_targets": "#FFAA00",
        "swimbladder_resonance": "#FF0088",
    }
    NOISE_COLORS = {
        "impulse_noise": "#FF0000",
        "background_noise": "#888888",
        "near_surface_interference": "#FFFF00",
        "attenuation": "#FF8800",
        "ring_down": "#FF00FF",
    }

    # Draw biological features
    for feat in analysis.biological_features:
        if feat.bounding_box is None:
            continue

        bb = feat.bounding_box
        x0 = left + (bb.x_percent[0] / 100.0) * plot_w
        x1 = left + (bb.x_percent[1] / 100.0) * plot_w
        y0 = top + (bb.y_percent[0] / 100.0) * plot_h
        y1 = top + (bb.y_percent[1] / 100.0) * plot_h

        color = COLORS.get(feat.type, "#FFFFFF")
        linewidth = 2.5 if feat.confidence == "high" else 1.5

        rect = mpatches.FancyBboxPatch(
            (x0, y0), x1 - x0, y1 - y0,
            boxstyle="round,pad=2",
            linewidth=linewidth,
            edgecolor=color,
            facecolor=color + "22",  # semi-transparent fill
        )
        ax.add_patch(rect)

        # Label
        label = feat.type.replace("_", " ").title()
        if feat.species_guess:
            label = f"{feat.species_guess}"
        if feat.confidence:
            label += f" ({feat.confidence})"

        ax.text(
            x0 + 4, y0 - 4,
            label,
            fontsize=14,
            color="white",
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.3", facecolor=color, alpha=0.8, edgecolor="none"),
            verticalalignment="bottom",
        )

    # Draw noise regions
    noise = analysis.noise_assessment or {}
    for region in noise.get("regions", []):
        bb_data = region if isinstance(region, dict) else None
        if bb_data and bb_data.get("bounding_box"):
            bb = bb_data["bounding_box"]
            x0 = left + (bb["x_percent"][0] / 100.0) * plot_w
            x1 = left + (bb["x_percent"][1] / 100.0) * plot_w
            y0 = top + (bb["y_percent"][0] / 100.0) * plot_h
            y1 = top + (bb["y_percent"][1] / 100.0) * plot_h

            color = NOISE_COLORS.get(bb_data.get("type", ""), "#888888")
            rect = mpatches.Rectangle(
                (x0, y0), x1 - x0, y1 - y0,
                linewidth=1.5, linestyle="--",
                edgecolor=color, facecolor="none",
            )
            ax.add_patch(rect)

    # Draw annotations
    for ann in analysis.annotations:
        pos = ann.get("position_percent", {})
        if pos:
            px = left + (pos.get("x", 50) / 100.0) * plot_w
            py = top + (pos.get("y", 50) / 100.0) * plot_h
            ax.text(
                px, py, ann.get("text", ""),
                fontsize=13, color="white", fontweight="bold",
                ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#333333", alpha=0.85, edgecolor="none"),
            )

    # Save
    if output_path is None:
        output_path = image_path.parent / f"{image_path.stem}--annotated{image_path.suffix}"
    else:
        output_path = Path(output_path)

    fig.savefig(output_path, dpi=dpi, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args():
    p = argparse.ArgumentParser(
        description="AI echogram analysis using Azure OpenAI vision models.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--image", type=Path, help="Single echogram PNG to analyze")
    g.add_argument("--dir", type=Path, help="Directory of echogram PNGs to analyze")

    p.add_argument("--frequency", type=int, default=None, help="Frequency in kHz")
    p.add_argument("--depth-range", default=None, help="Depth range (e.g. '10-500m')")
    p.add_argument("--context", default=None, help="Survey context string")
    p.add_argument("--lat", type=float, default=None, help="Latitude")
    p.add_argument("--lon", type=float, default=None, help="Longitude")
    p.add_argument("--model", default=None, help="Azure OpenAI deployment (e.g. gpt-4o, gpt-5)")
    p.add_argument("--no-overlay", action="store_true", help="Skip rendering annotated overlay")
    p.add_argument("--output-dir", type=Path, default=None, help="Output directory for results")
    return p.parse_args()


async def process_single(args, image_path: Path, output_dir: Path):
    """Analyze a single echogram and render overlay."""
    logger.info("Analyzing: %s", image_path.name)

    analysis = await analyze_echogram(
        image_path,
        frequency_khz=args.frequency,
        depth_range=args.depth_range,
        survey_context=args.context,
        latitude=args.lat,
        longitude=args.lon,
        deployment=args.model,
    )

    # Print summary
    logger.info("  Model: %s", analysis.model)
    if analysis.tokens:
        logger.info("  Tokens: %d prompt + %d completion",
                    analysis.tokens.get("prompt", 0), analysis.tokens.get("completion", 0))
    logger.info("  Summary: %s", analysis.summary[:200] if analysis.summary else "(no summary)")
    logger.info("  Features: %d biological, seafloor=%s, quality=%s",
                len(analysis.biological_features),
                "yes" if analysis.seafloor and analysis.seafloor.get("detected") else "no",
                (analysis.noise_assessment or {}).get("overall_quality", "?"))

    for feat in analysis.biological_features:
        logger.info("    [%s] %s — %s (conf: %s)",
                    feat.type, feat.species_guess or "unidentified",
                    feat.description[:80] if feat.description else "N/A",
                    feat.confidence)

    # Save JSON
    json_path = output_dir / f"{image_path.stem}--analysis.json"
    json_data = {
        "summary": analysis.summary,
        "biological_features": [
            {
                "type": f.type,
                "species_guess": f.species_guess,
                "confidence": f.confidence,
                "bounding_box": {
                    "x_percent": f.bounding_box.x_percent,
                    "y_percent": f.bounding_box.y_percent,
                } if f.bounding_box else None,
                "description": f.description,
                "estimated_density": f.estimated_density,
                "depth_range_m": f.depth_range_m,
                "sv_range_db": f.sv_range_db,
            }
            for f in analysis.biological_features
        ],
        "seafloor": analysis.seafloor,
        "noise_assessment": analysis.noise_assessment,
        "annotations": analysis.annotations,
        "recommendations": analysis.recommendations,
        "model": analysis.model,
        "tokens": analysis.tokens,
    }
    json_path.write_text(json.dumps(json_data, indent=2))
    logger.info("  Saved: %s", json_path.name)

    # Render overlay
    if not args.no_overlay and analysis.biological_features:
        overlay_path = output_dir / f"{image_path.stem}--annotated.png"
        render_overlay(image_path, analysis, overlay_path)
        logger.info("  Saved: %s", overlay_path.name)

    return analysis


async def main():
    from dotenv import load_dotenv
    load_dotenv()

    args = parse_args()

    # Collect image paths
    if args.image:
        images = [args.image]
        output_dir = args.output_dir or args.image.parent
    else:
        images = sorted(args.dir.glob("*.png"))
        output_dir = args.output_dir or args.dir
        if not images:
            logger.error("No PNG files found in %s", args.dir)
            sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Check credentials
    if not os.environ.get("AZURE_OPENAI_ENDPOINT") or not os.environ.get("AZURE_OPENAI_API_KEY"):
        logger.error("Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY environment variables")
        sys.exit(1)

    logger.info("Analyzing %d echogram(s) with %s",
                len(images), args.model or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"))

    for img_path in images:
        await process_single(args, img_path, output_dir)

    logger.info("Done — results in %s", output_dir)


if __name__ == "__main__":
    asyncio.run(main())
