from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from perpetual_engine.funding import fund_withdrawal
from perpetual_engine.io import load_config, load_snapshot, normalized_json, sha256_file, write_summary_csv
from perpetual_engine.models import DistributionState, FundingResult, RunResult, ZERO
from perpetual_engine.policy import evaluate_policy


def _empty_funding() -> FundingResult:
    return FundingResult(
        delivered_net_real=ZERO,
        cash_used_real=ZERO,
        total_outflow_real=ZERO,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="perpetual-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate = subparsers.add_parser("evaluate", help="evaluate Rule E and Funding Protocol G")
    evaluate.add_argument("--config", type=Path, required=True)
    evaluate.add_argument("--snapshot", type=Path, required=True)
    evaluate.add_argument("--out-dir", type=Path, required=True)
    evaluate.add_argument("--activation-date", type=date.fromisoformat)
    data = subparsers.add_parser("data", help="manage frozen official-source data")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    refresh = data_commands.add_parser("refresh", help="refresh and publish one frozen data vintage")
    refresh.add_argument("--config", type=Path, required=True)
    backtest = subparsers.add_parser("backtest", help="run the deterministic offline historical backtest")
    backtest.add_argument("--config", type=Path, required=True)
    backtest.add_argument("--output", type=Path, required=True)
    chronos = subparsers.add_parser("chronos", help="run the Chronos-2 forecast workflow")
    chronos_commands = chronos.add_subparsers(dest="chronos_command", required=True)
    worker = chronos_commands.add_parser("dashboard-evaluate-worker")
    worker.add_argument("--project-root", type=Path, required=True)
    worker.add_argument("--request", type=Path, required=True)
    worker.add_argument("--state", type=Path, required=True)
    worker.add_argument("--lock", type=Path, required=True)
    chronos_refresh = chronos_commands.add_parser("refresh")
    chronos_refresh.add_argument("--config", type=Path, required=True)
    chronos_forecast = chronos_commands.add_parser("forecast")
    chronos_forecast.add_argument("--config", type=Path, required=True)
    chronos_forecast.add_argument("--output", type=Path, required=True)
    chronos_evaluate = chronos_commands.add_parser("evaluate")
    chronos_evaluate.add_argument("--config", type=Path, required=True)
    chronos_evaluate.add_argument("--output", type=Path, required=True)
    chronos_reconcile = chronos_commands.add_parser("reconcile")
    chronos_reconcile.add_argument("--config", type=Path, required=True)
    chronos_reconcile.add_argument("--forecast-root", type=Path, required=True)
    chronos_reconcile.add_argument("--output", type=Path, required=True)
    risk_forecast = chronos_commands.add_parser("risk-forecast", help="run the daily Chronos shadow-risk forecast")
    risk_forecast.add_argument("--config", type=Path, required=True)
    risk_forecast.add_argument("--output", type=Path, required=True)
    risk_evaluate = chronos_commands.add_parser("risk-evaluate", help="run daily Chronos walk-forward evaluation")
    risk_evaluate.add_argument("--config", type=Path, required=True)
    risk_evaluate.add_argument("--output", type=Path, required=True)
    risk_evaluate.add_argument("--max-origins", type=int, default=36)
    portfolio_refresh = chronos_commands.add_parser("portfolio-refresh")
    portfolio_refresh.add_argument("--config", type=Path, required=True)
    portfolio_report = chronos_commands.add_parser("portfolio-report")
    portfolio_report.add_argument("--config", type=Path, required=True)
    portfolio_report.add_argument("--output", type=Path, required=True)
    portfolio_study_report = chronos_commands.add_parser("portfolio-study-report")
    portfolio_study_report.add_argument("--config", type=Path, required=True)
    portfolio_study_report.add_argument("--study", required=True)
    portfolio_study_report.add_argument("--output", type=Path, required=True)
    return parser


def evaluate_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    snapshot = load_snapshot(args.snapshot)
    policy = evaluate_policy(snapshot, config, args.activation_date)
    funding = (
        fund_withdrawal(snapshot, policy)
        if policy.state in (DistributionState.NORMAL, DistributionState.PROTECTED)
        else _empty_funding()
    )
    result = RunResult(
        config_version=config.version,
        policy=policy,
        funding=funding,
        input_hashes={
            "config": sha256_file(args.config),
            "snapshot": sha256_file(args.snapshot),
        },
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "result.json").write_bytes(normalized_json(result))
    write_summary_csv(result, args.out_dir / "summary.csv")
    print(
        f"{policy.lifecycle.value}/{policy.state.value}: "
        f"target={policy.target_gross_real} delivered={funding.delivered_net_real}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "chronos" and args.chronos_command == "dashboard-evaluate-worker":
        from perpetual_engine.chronos_job import run_evaluation_worker

        try:
            return run_evaluation_worker(args.project_root, args.request, args.state, args.lock)
        except (KeyError, OSError, TypeError, ValueError):
            print("Chronos worker input is invalid. Check local job paths and retry.", file=sys.stderr)
            return 2
    try:
        if args.command == "evaluate":
            return evaluate_command(args)
        if args.command == "data" and args.data_command == "refresh":
            from perpetual_engine.backtest_report import refresh_data

            return refresh_data(args.config)
        if args.command == "backtest":
            from perpetual_engine.backtest_report import run_backtest_report

            return run_backtest_report(args.config, args.output)
        if args.command == "chronos" and args.chronos_command == "refresh":
            from perpetual_engine.chronos_data import refresh_chronos_data

            print(refresh_chronos_data(args.config))
            return 0
        if args.command == "chronos" and args.chronos_command == "forecast":
            from perpetual_engine.chronos import publish_forecast

            print(publish_forecast(args.config, args.output))
            return 0
        if args.command == "chronos" and args.chronos_command == "evaluate":
            from perpetual_engine.chronos import evaluate_chronos

            print(evaluate_chronos(args.config, args.output))
            return 0
        if args.command == "chronos" and args.chronos_command == "reconcile":
            from perpetual_engine.chronos import reconcile_forecasts

            print(reconcile_forecasts(args.config, args.forecast_root, args.output))
            return 0
        if args.command == "chronos" and args.chronos_command == "risk-forecast":
            from perpetual_engine.chronos_risk import publish_current_risk_report

            print(publish_current_risk_report(args.config, args.output))
            return 0
        if args.command == "chronos" and args.chronos_command == "risk-evaluate":
            from perpetual_engine.chronos_risk import publish_walk_forward_evaluation

            print(publish_walk_forward_evaluation(args.config, args.output, max_origins=args.max_origins))
            return 0
        if args.command == "chronos" and args.chronos_command == "portfolio-refresh":
            from perpetual_engine.portfolio_monitor import refresh_portfolio_prices

            print(refresh_portfolio_prices(args.config))
            return 0
        if args.command == "chronos" and args.chronos_command == "portfolio-report":
            from perpetual_engine.portfolio_monitor import write_portfolio_report

            print(write_portfolio_report(args.config, args.output))
            return 0
        if args.command == "chronos" and args.chronos_command == "portfolio-study-report":
            from perpetual_engine.portfolio_monitor import write_etf_study_report

            print(write_etf_study_report(args.config, args.study, args.output))
            return 0
    except (KeyError, OSError, TypeError, ValueError) as exc:
        input_paths = [str(args.config)]
        input_paths.extend(
            str(getattr(args, name))
            for name in ("snapshot", "forecast_root", "output", "out_dir")
            if getattr(args, name, None) is not None
        )
        print(f"Input error ({', '.join(input_paths)}): {exc}", file=sys.stderr)
        return 2
    return 2
