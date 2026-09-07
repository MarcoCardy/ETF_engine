import json
import math
import os
from pathlib import Path

import numpy as np
import pytest


def test_daily_returns_and_future_realized_volatility_use_only_future_window():
    from perpetual_engine.chronos_risk import daily_returns, future_realized_volatility

    np.testing.assert_allclose(daily_returns([100.0, 102.0, 99.0]), [0.02, 99.0 / 102.0 - 1.0])
    returns = np.asarray([0.01, -0.02, 0.03, -0.01, 0.02])
    expected = [np.std(returns[index:index + 3], ddof=1) * math.sqrt(252) for index in range(3)]
    np.testing.assert_allclose(future_realized_volatility(returns, 3), expected)


def test_historical_and_ewma_volatility_baselines():
    from perpetual_engine.chronos_risk import ewma_volatility, historical_volatility, sigma_forecast

    returns = np.linspace(-0.03, 0.04, 70)
    sigma20 = np.std(returns[-20:], ddof=1) * math.sqrt(252)
    sigma60 = np.std(returns[-60:], ddof=1) * math.sqrt(252)
    assert historical_volatility(returns, 20) == pytest.approx(sigma20)
    assert historical_volatility(returns, 60) == pytest.approx(sigma60)
    assert sigma_forecast(sigma20, sigma60) == pytest.approx(0.35 * sigma20 + 0.65 * sigma60)

    variance = returns[0] ** 2
    for value in returns[1:]:
        variance = 0.94 * variance + 0.06 * value**2
    assert ewma_volatility(returns, 0.94) == pytest.approx(math.sqrt(variance * 252))


def test_sigma_risk_supports_median_and_conservative_quantiles():
    from perpetual_engine.chronos_risk import sigma_risk

    assert sigma_risk(0.10, 0.20, 0.30, (0.25, 0.35, 0.40)) == pytest.approx(0.215)
    assert sigma_risk(0.10, 0.20, 0.45, (0.25, 0.35, 0.40)) == pytest.approx(0.275)


@pytest.mark.parametrize(
    ("erp", "expected"),
    [(0.0299, 0.75), (0.03, 0.90), (0.04, 1.00), (0.05, 1.15), (0.06, 1.30)],
)
def test_beta_desired_boundaries(erp, expected):
    from perpetual_engine.chronos_risk import beta_desired

    assert beta_desired(erp) == expected


def test_kelly_trend_and_shadow_beta_can_only_reduce_risk():
    from perpetual_engine.chronos_risk import beta_comparison

    comparison = beta_comparison(
        erp=0.05,
        sigma_forecast_value=0.15,
        structural_volatility=0.16,
        chronos_vol_q90=0.24,
        trend_fast_positive=True,
        trend_slow_positive=False,
    )
    assert comparison.beta_desired == 1.15
    assert comparison.sigma_kelly == 0.16
    assert comparison.half_kelly == pytest.approx(0.05 / 0.16**2 / 2)
    assert comparison.beta_trend == 1.20
    assert comparison.beta_vol_production == 1.20
    assert comparison.beta_vol_chronos == 0.75
    assert comparison.beta_ceiling_chronos <= comparison.beta_ceiling_production
    assert comparison.beta_operational_chronos <= comparison.beta_operational_production
    assert comparison.chronos_impact == "REDUCE_RISK"


def test_portfolio_snapshot_reconciles_accounting_and_lwld_notional_exposure():
    from perpetual_engine.chronos_risk import load_portfolio_snapshot

    snapshot = load_portfolio_snapshot(Path("config/portfolio_snapshot_2026-08-17.json"))
    assert snapshot.total_eur == pytest.approx(617_320.80)
    assert snapshot.securities_eur == pytest.approx(615_369.90)
    assert snapshot.nominal_equity_eur == pytest.approx(487_001.10)
    assert snapshot.economic_equity_eur == pytest.approx(522_300.28)
    assert snapshot.nominal_equity_weight == pytest.approx(487_001.10 / 617_320.80)
    assert snapshot.economic_equity_weight == pytest.approx(0.8460759463)


def test_snapshot_is_replaceable_but_rejects_inconsistent_totals(tmp_path):
    from perpetual_engine.chronos_risk import load_portfolio_snapshot

    source = json.loads(Path("config/portfolio_snapshot_2026-08-17.json").read_text(encoding="utf-8"))
    source["total_eur"] += 1
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="total"):
        load_portfolio_snapshot(path)


def test_risk_config_centralizes_shadow_model_and_financial_constants():
    from perpetual_engine.chronos_risk import load_risk_config

    config = load_risk_config(Path("config/chronos_risk_v1.json"))
    assert config.mode == "SHADOW"
    assert config.horizons == (5, 10, 20)
    assert config.quantiles == (0.10, 0.25, 0.50, 0.75, 0.90)
    assert config.sigma_risk_weights == (0.25, 0.35, 0.40)
    assert config.ewma_lambda == 0.94
    assert config.price_config.name == "portfolio_p_v1.json"
    assert config.portfolio_snapshot.name == "portfolio_snapshot_2026-08-17.json"


def test_cutoff_context_and_forward_target_are_temporally_isolated():
    from perpetual_engine.chronos_risk import prepare_walk_forward_step

    dates = np.arange("2025-01-01", "2025-05-01", dtype="datetime64[D]")[:90]
    returns = np.linspace(-0.02, 0.02, len(dates))
    first = prepare_walk_forward_step(dates, returns, cutoff_index=69, horizon=10)
    changed = returns.copy()
    changed[70:] = 0.50
    second = prepare_walk_forward_step(dates, changed, cutoff_index=69, horizon=10)
    np.testing.assert_array_equal(first.context, second.context)
    assert first.data_cutoff == second.data_cutoff
    assert first.actual != second.actual
    assert first.context.flags.writeable is False


def test_horizon_targets_are_multivariate_return_and_realized_volatility():
    from perpetual_engine.chronos_risk import build_horizon_targets

    returns = np.asarray([0.01, -0.02, 0.03, 0.04])
    targets = build_horizon_targets(returns, 3)
    assert targets.shape == (2, 2)
    assert targets[0, -1] == pytest.approx(np.prod(1.0 + returns[-3:]) - 1.0)
    assert targets[1, -1] == pytest.approx(np.std(returns[-3:], ddof=1) * math.sqrt(252))
    assert targets.flags.writeable is False


def test_market_risk_forecast_batches_horizons_with_past_only_covariates():
    from perpetual_engine.chronos_risk import forecast_market_risk, load_risk_config

    config = load_risk_config(Path("config/chronos_risk_v1.json"))
    returns = np.linspace(-0.02, 0.025, 100)
    covariates = {"VIX": np.linspace(12.0, 22.0, 100)}
    calls = []

    def predictor(items, prediction_length, quantile_levels):
        calls.append((items, prediction_length, quantile_levels))
        assert all("future_covariates" not in item for item in items)
        assert all(item["target"].shape[0] == 2 for item in items)
        assert all(len(item["past_covariates"]["VIX"]) == item["target"].shape[1] for item in items)
        return [np.tile(
            np.asarray([[[-0.04, -0.02, 0.01, 0.03, 0.06]], [[0.12, 0.15, 0.18, 0.22, 0.28]]]),
            (1, prediction_length, 1),
        )]

    forecasts = forecast_market_risk(config, returns, covariates=covariates, predictor=predictor)
    assert len(calls) == 3
    assert [call[1] for call in calls] == [5, 10, 20]
    assert all(call[2] == list(config.quantiles) for call in calls)
    assert tuple(item.horizon for item in forecasts) == (5, 10, 20)
    assert forecasts[0].return_quantiles == (-0.04, -0.02, 0.01, 0.03, 0.06)
    assert forecasts[-1].volatility_quantiles == (0.12, 0.15, 0.18, 0.22, 0.28)


def test_pipeline_loading_is_forced_offline_and_restores_environment(monkeypatch):
    from perpetual_engine.chronos_risk import load_risk_config, load_risk_predictor
    from chronos import Chronos2Pipeline

    config = load_risk_config(Path("config/chronos_risk_v1.json"))
    seen = []

    class Pipeline:
        pass

    def load(*args, **kwargs):
        seen.append((os.environ.get("HF_HUB_OFFLINE"), kwargs))
        return Pipeline()

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setattr(Chronos2Pipeline, "from_pretrained", load)
    load_risk_predictor(config)
    assert seen[0][0] == "1"
    assert seen[0][1]["local_files_only"] is True
    assert "HF_HUB_OFFLINE" not in os.environ


def test_current_report_exposes_baselines_five_quantiles_and_no_trade_signal():
    from datetime import datetime, timezone
    from perpetual_engine.chronos_risk import build_current_risk_report, load_risk_config

    config = load_risk_config(Path("config/chronos_risk_v1.json"))
    returns = np.linspace(-0.02, 0.025, 100)
    dates = np.arange("2025-01-01", "2026-01-01", dtype="datetime64[D]")[:100]

    def predictor(items, prediction_length, quantile_levels):
        base = np.asarray([[[-0.04, -0.02, 0.01, 0.03, 0.06]], [[0.12, 0.15, 0.18, 0.22, 0.28]]])
        return [np.tile(base, (1, prediction_length, 1))]

    report = build_current_risk_report(
        config, dates, returns, dataset_version="d" * 64, predictor=predictor,
        issued_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )
    assert report["mode"] == "SHADOW"
    assert report["data_cutoff"] == str(dates[-1])
    assert set(report["baselines"]) == {"sigma20", "sigma60", "sigma_forecast", "ewma_094", "naive_persistence"}
    assert tuple(report["forecasts"]) == ("5d", "10d", "20d")
    assert set(report["forecasts"]["20d"]["return_quantiles"]) == {"Q10", "Q25", "Q50", "Q75", "Q90"}
    assert report["forecasts"]["20d"]["volatility_quantiles"]["Q90"] == 0.28
    assert report["chronos_vol_median"] == 0.18
    assert report["chronos_vol_conservative"] == 0.28
    assert report["risk_regime"] == "HIGH_RISK"
    assert "recommendation" not in report


def test_cli_accepts_separate_daily_risk_forecast_command():
    from perpetual_engine.cli import _parser

    args = _parser().parse_args([
        "chronos", "risk-forecast", "--config", "config/chronos_risk_v1.json",
        "--output", "outputs/chronos_risk_v1",
    ])
    assert args.chronos_command == "risk-forecast"
    assert args.config == Path("config/chronos_risk_v1.json")
