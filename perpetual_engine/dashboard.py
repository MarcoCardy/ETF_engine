from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

if sys.path and Path(sys.path[0]).resolve() == Path(__file__).resolve().parent:
    sys.path.pop(0)

import pandas as pd
import streamlit as st

from perpetual_engine.dashboard_service import (
    DashboardDataStatus,
    DashboardPaths,
    DashboardState,
    EtfCandidate,
    add_catalog_entry,
    current_data_status,
    exchange_for_ticker,
    load_dashboard_state,
    publish_dashboard_comparison,
    refresh_dashboard_data,
    remove_catalog_entry,
    reset_dashboard_state,
    save_dashboard_state,
    search_etfs,
    state_sha256,
)
from perpetual_engine.portfolio_monitor import ComponentSpec, load_portfolio_config


def _paths() -> DashboardPaths:
    configured = os.environ.get("ETF_DASHBOARD_ROOT")
    root = Path(configured).resolve() if configured else Path(__file__).resolve().parent.parent
    return DashboardPaths.from_root(root)


def _show_error(error: Exception) -> None:
    st.error("Operazione non completata. Controlla i dati e riprova.")
    with st.expander("Dettagli tecnici"):
        st.code(repr(error))


def _invalidate_comparison_cache() -> None:
    st.session_state.pop("comparison_cache", None)


def _holding_rows(state: DashboardState) -> list[dict[str, object]]:
    catalog = {item.study_id: item for item in state.catalog}
    return [
        {
            "ID": item.component_id,
            "Simbolo": item.ticker,
            "ISIN": item.isin,
            "Mercato": catalog.get(item.component_id).exchange if item.component_id in catalog else exchange_for_ticker(item.ticker),
            "Valuta": item.quote_currency,
            "Peso %": item.weight * 100,
        }
        for item in state.components
    ]


def _replace_portfolio_session(state: DashboardState) -> None:
    _invalidate_comparison_cache()
    st.session_state.portfolio_draft = _holding_rows(state)
    st.session_state.portfolio_editor_generation = st.session_state.get("portfolio_editor_generation", 0) + 1
    st.session_state.confirm_reset_requested = False


def _candidate_state(state: DashboardState, rows: list[dict[str, object]]) -> DashboardState:
    known = {item.component_id: item for item in state.components}
    known.update({item.study_id: item for item in state.catalog})
    components = []
    for row in rows:
        item = known.get(str(row["ID"]))
        if item is None:
            raise ValueError("strumento del portafoglio sconosciuto")
        components.append(ComponentSpec(item.study_id if hasattr(item, "study_id") else item.component_id, item.ticker, item.isin, item.quote_currency, float(row["Peso %"]) / 100))
    return DashboardState(tuple(components), state.catalog)


def _refresh(paths: DashboardPaths, state: DashboardState) -> None:
    st.session_state["refreshing"] = True
    succeeded = False
    try:
        with st.status("Aggiornamento dati...", expanded=True) as indicator:
            refresh_dashboard_data(
                paths,
                state,
                progress=lambda ticker: indicator.update(label=f"Aggiornamento dati: {ticker}"),
            )
        st.success("Dati aggiornati.")
        succeeded = True
    except Exception as error:
        _show_error(error)
    finally:
        st.session_state["refreshing"] = False
    if succeeded:
        _invalidate_comparison_cache()
        st.rerun()


def _render_status(paths: DashboardPaths, state: DashboardState) -> DashboardDataStatus | None:
    try:
        status = current_data_status(paths, state)
    except Exception as error:
        _show_error(error)
        status = None
    if status and status.available:
        st.sidebar.caption(f"Ultimo aggiornamento: {status.retrieved_at:%d/%m/%Y %H:%M}")
        st.sidebar.caption(f"Periodo comune: {status.first_month:%m/%Y} – {status.last_month:%m/%Y}")
    elif status:
        st.sidebar.warning(status.reason or "Dati non disponibili.")
    if st.sidebar.button("Aggiorna dati", key="refresh_data", disabled=st.session_state.get("refreshing", False)):
        _refresh(paths, state)
    return status


def _render_portfolio(paths: DashboardPaths, state: DashboardState) -> None:
    st.header("Portafoglio")
    if "portfolio_draft" not in st.session_state:
        st.session_state.portfolio_draft = _holding_rows(state)
    disabled = st.session_state.get("refreshing", False)
    draft = st.data_editor(
        pd.DataFrame(st.session_state.portfolio_draft),
        key=f"portfolio_editor_{st.session_state.get('portfolio_editor_generation', 0)}",
        disabled=True if disabled else ["ID", "Simbolo", "ISIN", "Mercato", "Valuta"],
        num_rows="fixed",
        column_config={"Peso %": st.column_config.NumberColumn("Peso %", min_value=0.0, format="%.2f")},
        width="stretch",
    )
    st.session_state.portfolio_draft = draft.to_dict("records")
    catalog = [item for item in state.catalog if item.study_id not in {row["ID"] for row in st.session_state.portfolio_draft}]
    add_id = st.selectbox("Aggiungi dal catalogo", [""] + [item.study_id for item in catalog], key="add_holding", disabled=disabled)
    if st.button("Aggiungi al portafoglio", key="add_holding_button", disabled=disabled or not add_id):
        item = next(item for item in catalog if item.study_id == add_id)
        st.session_state.portfolio_draft.append({
            "ID": item.study_id, "Simbolo": item.ticker, "ISIN": item.isin, "Mercato": item.exchange,
            "Valuta": item.quote_currency, "Peso %": 1.0,
        })
        st.rerun()
    removable = [row["ID"] for row in st.session_state.portfolio_draft]
    remove_id = st.selectbox("Rimuovi dal portafoglio", [""] + removable, key="remove_holding", disabled=disabled)
    if st.button("Rimuovi dal portafoglio", key="remove_holding_button", disabled=disabled or not remove_id):
        st.session_state.portfolio_draft = [row for row in st.session_state.portfolio_draft if row["ID"] != remove_id]
        st.rerun()
    if st.button("Salva portafoglio", key="save_portfolio", disabled=disabled):
        try:
            updated = _candidate_state(state, st.session_state.portfolio_draft)
            save_dashboard_state(paths.state, updated, default_config_path=paths.default_config)
            _invalidate_comparison_cache()
            st.session_state.portfolio_draft = _holding_rows(updated)
            st.success("Portafoglio salvato.")
        except Exception as error:
            _show_error(error)
    if st.button("Ripristina portafoglio predefinito", key="reset_portfolio", disabled=disabled):
        st.session_state.confirm_reset_requested = True
    if st.session_state.get("confirm_reset_requested") and st.button("Conferma ripristino", key="confirm_reset_button", disabled=disabled):
        try:
            restored = reset_dashboard_state(paths.default_config, paths.state)
            _replace_portfolio_session(restored)
            st.rerun()
        except Exception as error:
            _show_error(error)


def _render_etf(paths: DashboardPaths, state: DashboardState) -> None:
    st.header("ETF")
    st.dataframe(pd.DataFrame([item.__dict__ for item in state.catalog]), width="stretch", hide_index=True)
    disabled = st.session_state.get("refreshing", False)
    with st.form("etf_search"):
        st.text_input("Simbolo o ISIN", key="etf_query")
        searched = st.form_submit_button("Cerca", key="search_etf", disabled=disabled)
    if searched:
        st.session_state.etf_candidates = ()
        try:
            st.session_state.etf_candidates = search_etfs(st.session_state.etf_query)
        except Exception as error:
            _show_error(error)
    candidates: tuple[EtfCandidate, ...] = tuple(st.session_state.get("etf_candidates", ()))
    selected = st.selectbox(
        "Risultato da confermare",
        (None, *candidates),
        format_func=lambda item: "Seleziona un ETF" if item is None else f"{item.name} — {item.ticker} — {item.isin} — {item.exchange}",
        key="etf_candidate",
        disabled=disabled,
    )
    if st.button("Conferma ETF", key="confirm_etf", disabled=disabled or selected is None):
        try:
            updated = add_catalog_entry(state, selected)
            save_dashboard_state(paths.state, updated, default_config_path=paths.default_config)
            _invalidate_comparison_cache()
            st.session_state.etf_candidates = ()
            st.success("ETF aggiunto al catalogo.")
            st.rerun()
        except Exception as error:
            _show_error(error)
    remove_id = st.selectbox("Rimuovi ETF dal catalogo", [""] + [item.study_id for item in state.catalog], key="remove_catalog", disabled=disabled)
    if st.button("Rimuovi dal catalogo", key="remove_catalog_button", disabled=disabled or not remove_id):
        try:
            updated = remove_catalog_entry(state, remove_id)
            save_dashboard_state(paths.state, updated, default_config_path=paths.default_config)
            _invalidate_comparison_cache()
            st.success("ETF rimosso dal catalogo.")
            st.rerun()
        except Exception as error:
            _show_error(error)


def _csv_table(path: Path) -> pd.DataFrame:
    with path.open(newline="", encoding="utf-8") as source:
        table = pd.DataFrame(csv.DictReader(source))
    if "month" in table:
        table["month"] = pd.to_datetime(table["month"], format="%Y-%m-%d", errors="raise")
        numeric = table.columns.drop("month")
        table[numeric] = table[numeric].apply(pd.to_numeric, errors="raise")
    return table


def _render_comparison_report(output_dir: Path) -> None:
    monthly = _csv_table(output_dir / "comparison_monthly.csv")
    summary = _csv_table(output_dir / "comparison_summary.csv").iloc[0]
    contributions = _csv_table(output_dir / "component_contributions.csv")
    short_history = summary["SHORT_LIVE_HISTORY"].strip().lower() == "true"
    st.caption(f"Periodo del confronto: {summary['first_month']} – {summary['last_month']} ({summary['count']} rendimenti mensili)")
    if short_history:
        st.warning("Storico breve: rendimento annualizzato, volatilità e correlazione sono soltanto indicativi.")
    columns = st.columns(3)
    columns[0].metric("Rendimento ETF", f"{float(summary['etf_cumulative_return']):.2%}")
    columns[1].metric("Rendimento portafoglio", f"{float(summary['portfolio_cumulative_return']):.2%}")
    columns[2].metric("Correlazione", "n/d" if summary["correlation"] in ("", None) else f"{float(summary['correlation']):.2f}")
    annualized = st.columns(2)
    annualized[0].metric("Rendimento annualizzato ETF", f"{float(summary['etf_annualized_return']):.2%}")
    annualized[1].metric("Rendimento annualizzato portafoglio", f"{float(summary['portfolio_annualized_return']):.2%}")
    volatility = st.columns(2)
    volatility[0].metric("Volatilità annualizzata ETF", "n/d" if not summary["etf_annualized_volatility"] else f"{float(summary['etf_annualized_volatility']):.2%}")
    volatility[1].metric("Volatilità annualizzata portafoglio", "n/d" if not summary["portfolio_annualized_volatility"] else f"{float(summary['portfolio_annualized_volatility']):.2%}")
    drawdowns = st.columns(2)
    drawdowns[0].metric("Drawdown massimo ETF", f"{float(summary['etf_max_drawdown']):.2%}")
    drawdowns[1].metric("Drawdown massimo portafoglio", f"{float(summary['portfolio_max_drawdown']):.2%}")
    wins = st.columns(3)
    wins[0].metric("Mesi vinti ETF", summary["etf_winning_months"])
    wins[1].metric("Mesi vinti portafoglio", summary["portfolio_winning_months"])
    wins[2].metric("Mesi pari", summary["tied_months"])
    chart = monthly.set_index("month")
    st.subheader("Crescita di 100 euro")
    st.line_chart(chart[["etf_cumulative_value", "portfolio_cumulative_value"]])
    st.subheader("Volatilità mobile a 12 mesi")
    st.line_chart(chart[["etf_trailing_volatility_12m", "portfolio_trailing_volatility_12m"]])
    st.subheader("Drawdown")
    st.line_chart(chart[["etf_drawdown", "portfolio_drawdown"]])
    st.subheader("Rendimenti mensili")
    st.dataframe(monthly[["month", "etf_return", "portfolio_return"]], width="stretch", hide_index=True)
    st.subheader("Contributi dei componenti")
    st.dataframe(contributions, width="stretch", hide_index=True)
    for name in ("comparison_monthly.csv", "comparison_summary.csv", "component_contributions.csv"):
        st.download_button(f"Scarica {name}", data=(output_dir / name).read_bytes(), file_name=name, mime="text/csv")


def _render_comparisons(
    paths: DashboardPaths,
    state: DashboardState,
    status: DashboardDataStatus | None,
) -> None:
    st.header("Confronti")
    choices = {item.component_id: item.component_id for item in load_portfolio_config(paths.default_config).components}
    choices.update({item.study_id: f"{item.name} ({item.ticker})" for item in state.catalog})
    selected = st.selectbox("ETF da studiare", list(choices), format_func=choices.__getitem__, key="comparison_target")
    mode = st.radio(
        "Modalità di studio",
        ("Confronta con il portafoglio principale", "Portafoglio singolo (100%)"),
        key="comparison_mode",
    )
    st.caption("La modalità monostrumento studia l'ETF selezionato senza modificare il portafoglio salvato.")
    current_vintage = status.vintage_id if status is not None and status.available else None
    current_state = state_sha256(state)
    if st.button(
        "Calcola confronto",
        key="calculate_comparison",
        disabled=st.session_state.get("refreshing", False) or not current_vintage,
    ):
        try:
            st.session_state.comparison_cache = {
                "target": selected,
                "mode": mode,
                "state_sha256": current_state,
                "vintage_id": current_vintage,
                "output": str(publish_dashboard_comparison(paths, state, selected).output_dir),
            }
        except Exception as error:
            _show_error(error)
    cache = st.session_state.get("comparison_cache")
    output = cache.get("output") if (
        current_vintage
        and isinstance(cache, dict)
        and cache.get("target") == selected
        and cache.get("mode") == mode
        and cache.get("state_sha256") == current_state
        and cache.get("vintage_id") == current_vintage
    ) else None
    if output:
        try:
            st.caption(f"Studio corrente: {selected} — {mode}")
            _render_comparison_report(Path(output))
        except Exception as error:
            _show_error(error)


def _render_chronos(paths: DashboardPaths, status: DashboardDataStatus | None) -> None:
    st.header("Previsioni Chronos")
    st.info("Modulo sperimentale in modalità shadow: non modifica il portafoglio e non genera ordini.")
    risk_config = paths.project_root / "config" / "chronos_risk_v1.json"
    risk_output = paths.project_root / "outputs" / "chronos_risk_v1"
    from perpetual_engine.economic_report import economic_series_catalog

    st.subheader("Serie economiche disponibili")
    st.dataframe(pd.DataFrame(economic_series_catalog(paths.project_root)), hide_index=True, width="stretch")
    st.caption("Le serie sono aggiornate soltanto con il pulsante seguente; generare un report non usa Internet.")
    if st.button("Aggiorna dati economici", key="refresh_economic_data"):
        try:
            from perpetual_engine.economic_report import refresh_economic_data

            with st.status("Aggiornamento delle vintage economiche…", expanded=True):
                st.session_state.economic_vintages = refresh_economic_data(paths.project_root)
            st.session_state.pop("chronos_risk_report", None)
            st.success("Dati economici aggiornati e congelati.")
            st.rerun()
        except Exception as error:
            _show_error(error)
    if st.button("Genera report completo", key="calculate_chronos_report", disabled=status is None or not status.available):
        try:
            from perpetual_engine.chronos_risk import load_risk_config, load_risk_predictor, publish_current_risk_report

            with st.spinner("Calcolo locale in corso…"):
                config = load_risk_config(risk_config)
                st.session_state.chronos_risk_report = str(
                    publish_current_risk_report(
                        risk_config, risk_output, predictor=_cached_risk_predictor(str(risk_config), config.model_revision)
                    ) / "report.json"
                )
        except Exception as error:
            _show_error(error)
    _render_evaluation_job(paths)
    report_path = st.session_state.get("chronos_risk_report")
    if not report_path:
        st.caption("Premi il pulsante per usare esclusivamente i prezzi già salvati; nessun aggiornamento dati viene eseguito.")
        return
    try:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        baseline = report["baselines"]
        left, middle, right = st.columns(3)
        left.metric("Volatilità Chronos Q50 (20g)", f"{report['chronos_vol_median']:.2%}")
        middle.metric("Volatilità Chronos Q90 (20g)", f"{report['chronos_vol_conservative']:.2%}")
        right.metric("Regime diagnostico", report["risk_regime"])
        st.dataframe(pd.DataFrame([
            {"Misura": "sigma20", "Valore": baseline["sigma20"]},
            {"Misura": "sigma60", "Valore": baseline["sigma60"]},
            {"Misura": "EWMA 0,94", "Valore": baseline["ewma_094"]},
            {"Misura": "sigmaForecast", "Valore": baseline["sigma_forecast"]},
        ]), hide_index=True, width="stretch")
        for horizon, values in report["forecasts"].items():
            with st.expander(f"Orizzonte {horizon}"):
                st.dataframe(pd.DataFrame({
                    "Quantile": list(values["return_quantiles"]),
                    "Rendimento": list(values["return_quantiles"].values()),
                    "Volatilità": list(values["volatility_quantiles"].values()),
                }), hide_index=True, width="stretch")
        comparison = report.get("portfolio_comparison", {})
        st.subheader("Beta Chronos (shadow)")
        if comparison.get("status") == "AVAILABLE_SHADOW":
            st.dataframe(pd.DataFrame([{
                "betaDesired": comparison["beta_desired"],
                "betaCeiling produzione": comparison["beta_ceiling_production"],
                "beta operativo produzione": comparison["beta_operational_production"],
                "betaCeiling Chronos": comparison["beta_ceiling_chronos"],
                "beta operativo Chronos": comparison["beta_operational_chronos"],
                "Impatto": comparison["chronos_impact"],
            }]), hide_index=True, width="stretch")
        else:
            st.warning(f"Beta Chronos non disponibile: {comparison.get('reason', comparison.get('status', 'dati mancanti'))}")
        utility = report.get("portfolio_utility", {})
        st.subheader("Utilità storica del portafoglio")
        if "saved_portfolio_historical" in utility:
            st.dataframe(pd.DataFrame([utility["saved_portfolio_historical"]]), hide_index=True, width="stretch")
        st.caption("M0–M3 restano non disponibili finché non esistono traiettorie walk-forward di esposizione valide.")
        st.caption(f"Dati fino al {report['data_cutoff']} — modello {report['model']['id']} — nessuna probabilità di drawdown calcolata.")
    except Exception as error:
        _show_error(error)


@st.cache_resource(max_entries=2)
def _cached_risk_predictor(config_path: str, model_revision: str):
    del model_revision
    from perpetual_engine.chronos_risk import load_risk_config, load_risk_predictor

    return load_risk_predictor(load_risk_config(Path(config_path)))


def _render_evaluation_job(paths: DashboardPaths) -> None:
    from perpetual_engine.chronos_job import read_evaluation_job, start_evaluation_job
    from perpetual_engine.dashboard_chronos import read_direct_evaluation

    st.subheader("Ablation e p-value mensili")
    try:
        job = read_evaluation_job(paths)
    except Exception as error:
        _show_error(error)
        job = None
    if st.button("Avvia valutazione ablation", key="start_ablation_evaluation", disabled=job is not None and job.state in {"STARTING", "RUNNING"}):
        try:
            state = load_dashboard_state(paths.default_config, paths.state)
            job = start_evaluation_job(paths, state)
            st.rerun()
        except Exception as error:
            _show_error(error)
    if job is None:
        st.caption("Nessuna valutazione avviata.")
        return
    st.progress(job.progress, text=job.message)
    if job.state != "SUCCEEDED" or job.result_path is None:
        if job.state in {"FAILED", "INTERRUPTED"}:
            st.warning(job.message)
        return
    try:
        read_direct_evaluation(job.result_path, paths.chronos_output_root)
        contributions = pd.read_csv(job.result_path / "covariate_contribution.csv")
        significance = pd.read_csv(job.result_path / "monthly_significance.csv")
        selected = contributions[
            (contributions["scope"] == "CANDIDATE_PORTFOLIO")
            & (contributions["comparison"] == "CONDITIONAL")
            & (contributions["horizon"].astype(str) == "ALL")
        ]
        st.dataframe(selected, hide_index=True, width="stretch")
        if not significance.empty:
            latest = significance[significance["as_of_month"] == significance["as_of_month"].max()]
            st.caption("P-value one-sided dell’ultima fine mese disponibile; valori piccoli favoriscono la covariata.")
            st.dataframe(latest, hide_index=True, width="stretch")
    except Exception as error:
        _show_error(error)


def main(paths: DashboardPaths | None = None) -> None:
    paths = paths or _paths()
    st.set_page_config(page_title="Analisi ETF", layout="wide")
    st.title("Analisi ETF")
    try:
        state = load_dashboard_state(paths.default_config, paths.state)
    except Exception as error:
        _show_error(error)
        if st.button("Ripristina portafoglio predefinito", key="recover_state"):
            try:
                restored = reset_dashboard_state(paths.default_config, paths.state, preserve_catalog=False)
                _replace_portfolio_session(restored)
                st.rerun()
            except Exception as reset_error:
                _show_error(reset_error)
        return
    status = _render_status(paths, state)
    section = st.sidebar.radio("Sezione", ("Portafoglio", "ETF", "Confronti", "Previsioni Chronos"), key="section")
    if section == "Portafoglio":
        _render_portfolio(paths, state)
    elif section == "ETF":
        _render_etf(paths, state)
    elif section == "Confronti":
        _render_comparisons(paths, state, status)
    else:
        _render_chronos(paths, status)


if __name__ == "__main__":
    main()
