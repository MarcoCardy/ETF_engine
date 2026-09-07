from datetime import date, datetime, timezone
from decimal import Decimal

import numpy as np
import pytest


def test_point_in_time_macro_alignment_never_backfills_before_publication():
    from perpetual_engine.chronos_risk import align_point_in_time
    from perpetual_engine.point_in_time import ObservationRow

    def row(observed, available, value):
        return ObservationRow(
            series_id="CPI", observation_date=observed, period_end=observed,
            available_at=datetime.fromisoformat(available).replace(tzinfo=timezone.utc),
            value=Decimal(value), unit="index", source_url="https://example.test/cpi",
            retrieved_at=datetime(2026, 9, 1, tzinfo=timezone.utc), source_hash="a" * 64,
        )

    dates = np.asarray(["2025-01-10", "2025-01-15", "2025-02-14", "2025-02-15"], dtype="datetime64[D]")
    values = align_point_in_time(dates, (
        row(date(2024, 12, 31), "2025-01-15T00:00:00", "100"),
        row(date(2025, 1, 31), "2025-02-15T00:00:00", "101"),
    ), "CPI")
    assert np.isnan(values[0])
    np.testing.assert_allclose(values[1:], [100.0, 100.0, 101.0])


def test_walk_forward_context_is_expanding_and_future_mutation_cannot_change_inputs():
    from perpetual_engine.chronos_risk import load_risk_config, walk_forward_evaluate
    from pathlib import Path

    config = load_risk_config(Path("config/chronos_risk_v1.json"))
    dates = np.arange("2024-01-01", "2025-12-31", dtype="datetime64[D]")[:150]
    returns = np.linspace(-0.02, 0.02, len(dates))
    contexts = []
    calls = []

    def predictor(items, prediction_length, quantile_levels):
        calls.append((len(items), prediction_length))
        contexts.extend(item["target"].copy() for item in items)
        base = np.asarray([[[-0.03, -0.01, 0.0, 0.01, 0.03]], [[0.10, 0.12, 0.15, 0.18, 0.22]]])
        return [np.tile(base, (1, prediction_length, 1)) for _item in items]

    rows = walk_forward_evaluate(config, dates, returns, predictor=predictor, min_context=70, origin_step=20)
    assert rows
    assert all(row.forecast_cutoff < row.target_end for row in rows)
    assert all(row.external_scaling == "NONE" for row in rows)
    assert all(row.sigma20 > 0 and row.sigma60 > 0 and row.ewma > 0 for row in rows)
    assert all(row.zero_return == 0.0 for row in rows)
    assert calls == [(4, 5), (4, 10), (4, 20)]
    assert all(contexts[index].shape[1] <= contexts[index + 1].shape[1] for index in (0, 1, 2, 4, 5, 6, 8, 9, 10))

    contexts.clear()
    calls.clear()
    latest = walk_forward_evaluate(config, dates, returns, predictor=predictor, min_context=70, origin_step=20, max_origins=1)
    assert calls == [(1, 5), (1, 10), (1, 20)]
    assert {row.forecast_cutoff for row in latest} == {dates[129]}


def test_probabilistic_metrics_report_loss_calibration_and_downside_performance():
    from perpetual_engine.chronos_risk import probabilistic_metrics

    actual = np.asarray([-0.04, -0.01, 0.02, 0.05])
    forecasts = np.asarray([
        [-0.05, -0.04, -0.02, 0.00, 0.02],
        [-0.03, -0.02, 0.00, 0.02, 0.04],
        [-0.01, 0.00, 0.02, 0.04, 0.06],
        [0.01, 0.03, 0.05, 0.07, 0.09],
    ])
    metrics = probabilistic_metrics(actual, forecasts, (0.1, 0.25, 0.5, 0.75, 0.9), target="RETURN")
    assert metrics["mae_q50"] == pytest.approx(0.0075)
    assert metrics["rmse_q50"] == pytest.approx(np.sqrt(0.000125))
    assert metrics["sign_accuracy"] == pytest.approx(0.75)
    assert metrics["coverage_q50"] == pytest.approx(1.0)
    assert metrics["downside_mae_q50"] == pytest.approx(0.015)
    assert metrics["mean_pinball_loss"] > 0


def test_portfolio_risk_utility_includes_requested_non_pathological_metrics():
    from perpetual_engine.chronos_risk import portfolio_risk_utility

    metrics = portfolio_risk_utility([0.01, -0.02, 0.015, -0.01, 0.02], turnover=0.25)
    assert set(metrics) == {
        "annualized_return", "annualized_volatility", "downside_deviation", "sharpe",
        "sortino", "calmar", "max_drawdown", "cvar_05", "turnover",
    }
    assert metrics["max_drawdown"] < 0
    assert metrics["cvar_05"] <= 0
    assert metrics["turnover"] == 0.25


def test_ablation_groups_are_cumulative_and_feature_availability_is_explicit():
    from perpetual_engine.chronos_risk import ABLATION_GROUPS, FEATURE_CLASSIFICATION

    assert tuple(ABLATION_GROUPS) == ("A_MARKET_ONLY", "B_MARKET_VOLATILITY", "C_RATES", "D_VALUATION", "E_FACTORS", "F_FX", "G_FULL")
    assert ABLATION_GROUPS["A_MARKET_ONLY"] == ()
    assert set(ABLATION_GROUPS["F_FX"]).issubset(ABLATION_GROUPS["G_FULL"])
    assert FEATURE_CLASSIFICATION["VIX"] == "HIGH_FREQUENCY_OBSERVED"
    assert FEATURE_CLASSIFICATION["DAMODARAN_ERP"] == "SLOW_MOVING_MACRO"
    assert "KNOWN_FUTURE" not in set(FEATURE_CLASSIFICATION.values())


def test_walk_forward_summary_compares_chronos_with_simple_baselines():
    from pathlib import Path
    from perpetual_engine.chronos_risk import load_risk_config, summarize_walk_forward, walk_forward_evaluate

    config = load_risk_config(Path("config/chronos_risk_v1.json"))
    dates = np.arange("2024-01-01", "2026-01-01", dtype="datetime64[D]")[:150]
    returns = np.sin(np.arange(150)) / 100

    def predictor(items, prediction_length, quantile_levels):
        base = np.asarray([[[-0.03, -0.01, 0.0, 0.01, 0.03]], [[0.08, 0.10, 0.12, 0.15, 0.18]]])
        return [np.tile(base, (1, prediction_length, 1)) for _item in items]

    rows = walk_forward_evaluate(config, dates, returns, predictor=predictor, min_context=70, origin_step=20)
    summary = summarize_walk_forward(rows, config.quantiles)
    assert set(summary) == {"5d", "10d", "20d"}
    assert set(summary["20d"]["volatility_baselines"]) == {"sigma20", "sigma60", "sigma_forecast", "ewma_094", "naive_persistence"}
    assert set(summary["20d"]["return_baselines"]) == {"zero", "historical_mean", "momentum_persistence"}
    assert "coverage_q90" in summary["20d"]["chronos_volatility"]


def test_cli_accepts_walk_forward_risk_evaluation_command():
    from pathlib import Path
    from perpetual_engine.cli import _parser

    args = _parser().parse_args([
        "chronos", "risk-evaluate", "--config", "config/chronos_risk_v1.json",
        "--output", "outputs/chronos_risk_v1/evaluations", "--max-origins", "36",
    ])
    assert args.chronos_command == "risk-evaluate"
    assert args.max_origins == 36
    assert args.output == Path("outputs/chronos_risk_v1/evaluations")


def test_walk_forward_publication_writes_predictions_metrics_and_manifest(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from perpetual_engine.chronos_risk import publish_walk_forward_evaluation
    from perpetual_engine import portfolio_monitor

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config = json.loads(Path("config/chronos_risk_v1.json").read_text(encoding="utf-8"))
    config_path = config_dir / "chronos_risk_v1.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    days = np.arange("2024-01-01", "2025-01-01", dtype="datetime64[D]")[:100]
    prices = {date.fromisoformat(str(day)): 100.0 + index for index, day in enumerate(days)}
    portfolio = SimpleNamespace(components=(SimpleNamespace(component_id="SWDA", ticker="SWDA.MI"),))
    monkeypatch.setattr(portfolio_monitor, "load_current_portfolio_prices", lambda *args, **kwargs: (
        portfolio, {"SWDA.MI": prices}, {"vintage_id": "d" * 64, "retrieved_at": "2026-09-01T00:00:00+00:00"},
    ))

    def predictor(items, prediction_length, quantile_levels):
        base = np.asarray([[[-0.03, -0.01, 0.0, 0.01, 0.03]], [[0.08, 0.10, 0.12, 0.15, 0.18]]])
        return [np.tile(base, (1, prediction_length, 1)) for _item in items]

    result = publish_walk_forward_evaluation(config_path, tmp_path / "outputs", predictor=predictor, min_context=60, origin_step=10, max_origins=2)
    assert {path.name for path in result.iterdir()} == {"predictions.csv", "metrics.json", "manifest.json"}
    manifest = json.loads((result / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "SHADOW"
    assert manifest["origin_count"] == 2
    assert manifest["generated_sha256"].keys() == {"metrics.json", "predictions.csv"}
