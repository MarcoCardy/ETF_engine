from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np

from perpetual_engine.point_in_time import ObservationRow


def _row(day: date, available: datetime, value: str) -> ObservationRow:
    from decimal import Decimal

    return ObservationRow(
        series_id="TEST",
        observation_date=day,
        period_end=day,
        available_at=available,
        value=Decimal(value),
        unit="index",
        source_url="https://example.test/series.csv",
        retrieved_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        source_hash="a" * 64,
        quality_flags=("TEST",),
    )


def test_catalog_lists_requested_series_when_vintages_are_missing(tmp_path):
    from perpetual_engine.economic_report import economic_series_catalog

    config = tmp_path / "config"
    config.mkdir()
    (config / "chronos_v1.json").write_text(
        '{"sources":[{"id":"ECB_DFR","url":"https://example.test/ecb.csv","role":"known_future"}]}',
        encoding="utf-8",
    )
    (config / "data_sources_v1.json").write_text(
        '{"sources":['
        '{"source_id":"FRED_VIXCLS","url":"https://example.test/vix.csv"},'
        '{"source_id":"FRED_T10YIE","url":"https://example.test/t10yie.csv"},'
        '{"source_id":"DAMODARAN_ERP_MONTHLY","url":"https://example.test/erp.xlsx"}'
        ']}',
        encoding="utf-8",
    )

    rows = economic_series_catalog(tmp_path)
    by_name = {row["series"]: row for row in rows}

    assert by_name["VIX"]["status"] == "MISSING_VINTAGE"
    assert by_name["US_10Y_BREAKEVEN"]["status"] == "MISSING_VINTAGE"
    assert by_name["DAMODARAN_ERP"]["source"] == "DAMODARAN_ERP_MONTHLY"
    assert by_name["MSCI_WORLD_FORWARD_EARNINGS_YIELD"]["status"] == "UNAVAILABLE_NO_POINT_IN_TIME_SOURCE"


def test_alignment_uses_publication_timestamp_not_observation_date():
    from perpetual_engine.economic_report import align_rows_asof

    rows = (
        _row(
            date(2024, 1, 31),
            datetime(2024, 2, 15, 12, tzinfo=timezone.utc),
            "100",
        ),
    )
    dates = np.asarray(["2024-02-14", "2024-02-15", "2024-02-16"], dtype="datetime64[D]")

    values = align_rows_asof(dates, rows, "TEST")

    assert np.isnan(values[0])
    assert values[1:].tolist() == [100.0, 100.0]


def test_general_source_config_declares_vix_and_direct_breakeven():
    import json
    from pathlib import Path

    config = json.loads((Path(__file__).parents[1] / "config" / "data_sources_v1.json").read_text(encoding="utf-8"))
    sources = {item["source_id"]: item for item in config["sources"]}

    assert sources["FRED_VIXCLS"]["series_id"] == "VIXCLS"
    assert sources["FRED_T10YIE"]["series_id"] == "T10YIE"


def test_risk_inputs_load_frozen_series_and_current_erp(tmp_path):
    import json
    from perpetual_engine.point_in_time import write_observation_csv

    config = tmp_path / "config"
    config.mkdir()
    (config / "data_sources_v1.json").write_text(
        '{"sources":['
        '{"source_id":"FRED_VIXCLS","url":"https://example.test/vix.csv"},'
        '{"source_id":"DAMODARAN_ERP_MONTHLY","url":"https://example.test/erp.xlsx"}'
        ']}', encoding="utf-8",
    )
    (config / "chronos_v1.json").write_text('{"sources":[]}', encoding="utf-8")
    frozen = tmp_path / "data" / "frozen"
    vintage = frozen / "vintages" / ("b" * 64) / "normalized"
    vintage.mkdir(parents=True)
    base = _row(date(2024, 1, 31), datetime(2024, 2, 1, tzinfo=timezone.utc), "18")
    vix = ObservationRow(**{**base.__dict__, "series_id": "VIXCLS"})
    erp = ObservationRow(**{
        **vix.__dict__, "series_id": "DAMODARAN_ERP_T12M", "value": __import__("decimal").Decimal("0.045")
    })
    write_observation_csv((vix,), vintage / "vix.csv")
    write_observation_csv((erp,), vintage / "erp.csv")
    manifest = {
        "vintage_id": "b" * 64,
        "retrieved_at": "2024-02-02T00:00:00+00:00",
        "sources": [
            {"source_id": "FRED_VIXCLS", "normalized_path": f"vintages/{'b' * 64}/normalized/vix.csv"},
            {"source_id": "DAMODARAN_ERP_MONTHLY", "normalized_path": f"vintages/{'b' * 64}/normalized/erp.csv"},
        ],
    }
    frozen.mkdir(parents=True, exist_ok=True)
    (frozen / "current_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    from perpetual_engine.economic_report import load_risk_covariates

    result = load_risk_covariates(tmp_path, np.asarray(["2024-02-01", "2024-02-02"], dtype="datetime64[D]"))

    assert result.covariates["VIX"].tolist() == [18.0, 18.0]
    assert result.erp == 0.045
