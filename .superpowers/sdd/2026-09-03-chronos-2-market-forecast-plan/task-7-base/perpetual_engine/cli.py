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
    try:
        if args.command == "evaluate":
            return evaluate_command(args)
        if args.command == "data" and args.data_command == "refresh":
            from perpetual_engine.backtest_report import refresh_data

            return refresh_data(args.config)
        if args.command == "backtest":
            from perpetual_engine.backtest_report import run_backtest_report

            return run_backtest_report(args.config, args.output)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        input_paths = [str(args.config)]
        input_paths.extend(
            str(getattr(args, name))
            for name in ("snapshot", "output", "out_dir")
            if getattr(args, name, None) is not None
        )
        print(f"Input error ({', '.join(input_paths)}): {exc}", file=sys.stderr)
        return 2
    return 2
