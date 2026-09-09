from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import stat
from datetime import date
from pathlib import Path
from typing import Any

from perpetual_engine.models import (
    CENT,
    CpiObservation,
    Holding,
    PolicyConfig,
    PortfolioSnapshot,
    RunResult,
    TaxCategory,
    money,
    to_primitive,
)


def _date(value: Any, field_name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO date string")
    return date.fromisoformat(value)


def _decimal(value: Any, field_name: str):
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a JSON string")
    return money(value)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    return data


def load_config(path: Path) -> PolicyConfig:
    data = _load_json(path)
    if "protected_monthly_net" in data:
        raise KeyError("protected_monthly_net is no longer supported; use protected_monthly_gross")
    return PolicyConfig(
        version=str(data["version"]),
        base_date=_date(data["base_date"], "base_date"),
        base_real_capital=_decimal(data["base_real_capital"], "base_real_capital"),
        initial_hwm=_decimal(data["initial_hwm"], "initial_hwm"),
        hard_floor=_decimal(data["hard_floor"], "hard_floor"),
        activation_threshold=_decimal(data["activation_threshold"], "activation_threshold"),
        ratchet_hwm_step=_decimal(data["ratchet_hwm_step"], "ratchet_hwm_step"),
        ratchet_floor_increment=_decimal(data["ratchet_floor_increment"], "ratchet_floor_increment"),
        protected_monthly_gross=_decimal(data["protected_monthly_gross"], "protected_monthly_gross"),
        normal_annual_rate=_decimal(data["normal_annual_rate"], "normal_annual_rate"),
        cpi_series=str(data["cpi_series"]),
    )


def _cpi(data: Any, field_name: str) -> CpiObservation | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError(f"{field_name} must be an object or null")
    return CpiObservation(
        series=str(data["series"]),
        observation_date=_date(data["observation_date"], f"{field_name}.observation_date"),
        publication_date=_date(data["publication_date"], f"{field_name}.publication_date"),
        value=_decimal(data["value"], f"{field_name}.value"),
    )


def _holding(data: dict[str, Any]) -> Holding:
    permitted = data["sale_permitted"]
    if not isinstance(permitted, bool):
        raise ValueError("sale_permitted must be true or false")
    holding = Holding(
        symbol=str(data["symbol"]),
        isin=str(data["isin"]),
        role=str(data["role"]),
        quantity=_decimal(data["quantity"], f"{data['symbol']}.quantity"),
        price_eur=_decimal(data["price_eur"], f"{data['symbol']}.price_eur"),
        weighted_average_cost_eur=(
            None
            if data["weighted_average_cost_eur"] is None
            else _decimal(data["weighted_average_cost_eur"], f"{data['symbol']}.weighted_average_cost_eur")
        ),
        price_multiplier=_decimal(data["price_multiplier"], f"{data['symbol']}.price_multiplier"),
        sale_increment=_decimal(data["sale_increment"], f"{data['symbol']}.sale_increment"),
        tax_category=TaxCategory(data["tax_category"]),
        tax_rate=_decimal(data["tax_rate"], f"{data['symbol']}.tax_rate"),
        funding_priority=int(data["funding_priority"]),
        spread_bps=_decimal(data["spread_bps"], f"{data['symbol']}.spread_bps"),
        commission_eur=_decimal(data["commission_eur"], f"{data['symbol']}.commission_eur"),
        minimum_weight=_decimal(data["minimum_weight"], f"{data['symbol']}.minimum_weight"),
        sale_permitted=permitted,
        price_date=_date(data["price_date"], f"{data['symbol']}.price_date"),
        price_currency=str(data["price_currency"]),
    )
    stated_value = _decimal(data["market_value_eur"], f"{data['symbol']}.market_value_eur")
    if abs(stated_value - holding.market_value_eur) > CENT:
        raise ValueError(f"{holding.symbol}: market_value_eur does not reconcile")
    return holding


def load_snapshot(path: Path) -> PortfolioSnapshot:
    data = _load_json(path)
    holdings_data = data["holdings"]
    if not isinstance(holdings_data, list):
        raise ValueError("holdings must be an array")
    return PortfolioSnapshot(
        valuation_date=_date(data["valuation_date"], "valuation_date"),
        price_date=_date(data["price_date"], "price_date"),
        nominal_capital_eur=_decimal(data["nominal_capital_eur"], "nominal_capital_eur"),
        cash_eur=_decimal(data["cash_eur"], "cash_eur"),
        operational_cash_minimum_eur=_decimal(
            data["operational_cash_minimum_eur"], "operational_cash_minimum_eur"
        ),
        holdings=tuple(_holding(item) for item in holdings_data),
        cpi_base=_cpi(data.get("cpi_base"), "cpi_base"),
        cpi_current=_cpi(data.get("cpi_current"), "cpi_current"),
        previous_hwm_real=_decimal(data["previous_hwm_real"], "previous_hwm_real"),
    )


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def normalized_json(result: RunResult) -> bytes:
    return canonical_json(to_primitive(result))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def remove_tree(path: Path) -> None:
    """Remove a tree even when OneDrive marks a child as read-only on Windows."""

    def clear_readonly(function, target, error):
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onexc=clear_readonly)


def write_summary_csv(result: RunResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "config_version": result.config_version,
        "lifecycle": result.policy.lifecycle.value,
        "state": result.policy.state.value,
        "real_capital": format(result.policy.real_capital, "f"),
        "hwm_real": format(result.policy.hwm_real, "f"),
        "ratcheted_floor_real": format(result.policy.ratcheted_floor_real, "f"),
        "target_gross_real": format(result.policy.target_gross_real, "f"),
        "delivered_net_real": format(result.funding.delivered_net_real, "f"),
        "total_outflow_real": format(result.funding.total_outflow_real, "f"),
        "errors": " | ".join(result.errors + result.policy.errors + result.funding.errors),
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
