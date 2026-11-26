"""Adapter for DAX Studio CLI (`dscmd.exe`) VPAX exports and DAX benchmarks."""

from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bi_agent.adapters._subprocess import resolve_dscmd, run_command
from bi_agent.config import ToolchainConfig


class VpaxResult(BaseModel):
    ok: bool
    vpax_path: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    raw_output: str = ""


class BenchmarkResult(BaseModel):
    ok: bool
    cold_avg_ms: float | None = None
    warm_avg_ms: float | None = None
    cold_max_ms: float | None = None
    warm_max_ms: float | None = None
    csv_path: str | None = None
    error: str = ""
    raw_output: str = ""


def _pick(mapping: dict[str, Any], *keys: str, default: Any = 0) -> Any:
    lowered = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return default


def parse_vpax_metrics(vpax_path: Path) -> dict[str, Any]:
    """Extract VertiPaq Analyzer summary metrics from a `.vpax` ZIP archive."""
    if not vpax_path.is_file():
        raise FileNotFoundError(f"VPAX archive not found: {vpax_path}")

    with zipfile.ZipFile(vpax_path, "r") as zf:
        target_member = None
        for name in zf.namelist():
            if name.lower().endswith("daxvpaview.json"):
                target_member = name
                break
        if target_member is None:
            raise ValueError(f"DaxVpaView.json not found inside VPAX archive: {vpax_path}")
        payload = json.loads(zf.read(target_member).decode("utf-8-sig"))

    tables = _pick(payload, "Tables", "tables", default=[]) or []
    columns = _pick(payload, "Columns", "columns", default=[]) or []

    total_rows = 0
    total_table_bytes = 0
    for tbl in tables:
        if isinstance(tbl, dict):
            total_rows += int(_pick(tbl, "RowsCount", "RowCount", "rows", default=0) or 0)
            total_table_bytes += int(
                _pick(tbl, "TableSize", "TotalSize", "Size", default=0) or 0
            )

    column_sizes: list[dict[str, Any]] = []
    total_col_bytes = 0
    for col in columns:
        if not isinstance(col, dict):
            continue
        col_bytes = int(_pick(col, "TotalSize", "TotalBytes", "Size", default=0) or 0)
        if col_bytes == 0:
            col_bytes = int(_pick(col, "DataSize", default=0) or 0) + int(
                _pick(col, "DictionarySize", default=0) or 0
            ) + int(_pick(col, "HierarchySize", default=0) or 0)
        total_col_bytes += col_bytes
        column_sizes.append(
            {
                "table": str(_pick(col, "TableName", "Table", default="")),
                "column": str(_pick(col, "ColumnName", "Name", default="")),
                "total_size_bytes": col_bytes,
                "cardinality": int(_pick(col, "ColumnCardinality", "Cardinality", default=0) or 0),
            }
        )

    column_sizes.sort(key=lambda item: item["total_size_bytes"], reverse=True)
    return {
        "table_count": len(tables),
        "column_count": len(columns),
        "total_rows": total_rows,
        "total_size_bytes": total_table_bytes if total_table_bytes > 0 else total_col_bytes,
        "top_columns": column_sizes[:5],
    }


def export_vpax(
    server: str,
    database: str,
    output_path: Path,
    config: ToolchainConfig,
) -> VpaxResult:
    """Export a VPAX archive using `dscmd.exe vpax` and summarize VertiPaq metrics."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dscmd_bin = resolve_dscmd(config)
    res = run_command(
        [dscmd_bin, "vpax", str(output_path), "-s", server, "-d", database],
        timeout=config.timeouts_seconds.dscmd,
    )
    if res.returncode != 0 or not output_path.is_file():
        return VpaxResult(
            ok=False,
            vpax_path=str(output_path),
            error=res.combined_output or f"dscmd vpax failed with exit {res.returncode}",
            raw_output=res.combined_output,
        )
    try:
        metrics = parse_vpax_metrics(output_path)
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        return VpaxResult(
            ok=False,
            vpax_path=str(output_path),
            error=str(exc),
            raw_output=res.combined_output,
        )
    return VpaxResult(
        ok=True,
        vpax_path=str(output_path),
        metrics=metrics,
        raw_output=res.combined_output,
    )


def _parse_benchmark_csv(
    csv_path: Path,
) -> tuple[float | None, float | None, float | None, float | None]:
    if not csv_path.is_file():
        return None, None, None, None
    cold_vals: list[float] = []
    warm_vals: list[float] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            lowered = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            seq_val = lowered.get("sequence", "")
            if seq_val.startswith("#") or seq_val.lower() == "cache":
                break
            cache_mode = lowered.get("cache", lowered.get("type", "")).lower()
            dur_str = lowered.get(
                "totalduration_ms",
                lowered.get(
                    "totalduration",
                    lowered.get("durationms", lowered.get("totalms", lowered.get("duration", ""))),
                ),
            )
            if not dur_str:
                continue
            try:
                dur = float(dur_str)
            except ValueError:
                continue
            if "cold" in cache_mode:
                cold_vals.append(dur)
            elif "warm" in cache_mode:
                warm_vals.append(dur)
    cold_avg = round(sum(cold_vals) / len(cold_vals), 2) if cold_vals else None
    warm_avg = round(sum(warm_vals) / len(warm_vals), 2) if warm_vals else None
    cold_max = round(max(cold_vals), 2) if cold_vals else None
    warm_max = round(max(warm_vals), 2) if warm_vals else None
    return cold_avg, warm_avg, cold_max, warm_max


def run_benchmark(
    server: str,
    database: str,
    dax_file: Path,
    output_csv: Path,
    config: ToolchainConfig,
    cold_runs: int = 2,
    warm_runs: int = 3,
) -> BenchmarkResult:
    """Run DAX Studio CLI benchmark (`dscmd.exe benchmark`) and extract cold/warm timings."""
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    dscmd_bin = resolve_dscmd(config)
    res = run_command(
        [
            dscmd_bin,
            "benchmark",
            str(output_csv),
            "-s",
            server,
            "-d",
            database,
            "-f",
            str(dax_file),
            "--cold",
            str(cold_runs),
            "--warm",
            str(warm_runs),
        ],
        timeout=config.timeouts_seconds.dscmd,
    )
    cold_avg, warm_avg, cold_max, warm_max = _parse_benchmark_csv(output_csv)
    if cold_avg is None or warm_avg is None:
        cold_match = re.search(r"Cold[^\d]*(\d+(?:\.\d+)?)\s*ms", res.combined_output, re.I)
        warm_match = re.search(r"Warm[^\d]*(\d+(?:\.\d+)?)\s*ms", res.combined_output, re.I)
        if cold_avg is None and cold_match:
            cold_avg = float(cold_match.group(1))
        if warm_avg is None and warm_match:
            warm_avg = float(warm_match.group(1))

    ok = res.returncode == 0
    return BenchmarkResult(
        ok=ok,
        cold_avg_ms=cold_avg,
        warm_avg_ms=warm_avg,
        cold_max_ms=cold_max,
        warm_max_ms=warm_max,
        csv_path=str(output_csv),
        error="" if ok else (res.combined_output or f"dscmd benchmark exited {res.returncode}"),
        raw_output=res.combined_output,
    )
