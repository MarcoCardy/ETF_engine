### Task 2: Covariate parsing and point-in-time monthly alignment

**Files:**
- Modify: `config/chronos_v1.json`
- Modify: `perpetual_engine/chronos_data.py`
- Modify: `tests/test_chronos.py`

**Interfaces:**
- Consumes: `SourceArtifact`, `parse_fred_csv()`, and `treasury_available_at()` from `perpetual_engine.data_sources`.
- Produces: `parse_ecb_dfr(artifact: SourceArtifact) -> tuple[ObservationRow, ...]`, `parse_bis_gli(artifact: SourceArtifact) -> tuple[ObservationRow, ...]`, and `normalize_covariates(config: ChronosConfig, target_months: tuple[date, ...], rows_by_id: Mapping[str, tuple[ObservationRow, ...]]) -> MonthlyTable`.
- Mid-plan approved addition: FRED `CPIAUCNS` becomes `US_CPI_YOY`, calculated as headline year-on-year percent and available only at the end of the following month. Add it to configuration, exact source-role validation, parsing/normalization, and tests. It is historical-only.

- [ ] **Step 1: Write failing parser and availability-boundary tests**

Create tiny raw fixtures for all five sources. Assert:

- ECB uses the last effective rate on or before each month end;
- DGS10 values `4.20` and `4.30` produce a monthly mean of `4.25`, never `0.0425`;
- a DGS10 month-end observation is excluded when its `treasury_available_at()` is after the monthly cut-off;
- Brent uses `log(mean_t / mean_t_minus_1)` and excludes observations until seven calendar days after observation;
- BIS accepts only `Q.USD.3P.N.A.I.B.771`, marks the quarter available four months after quarter end, then carries the released value monthly without interpolation;
- CPI accepts only `CPIAUCNS`, computes `100 * (CPI_t / CPI_t_minus_12 - 1)`, and is unavailable until the end of the following calendar month;
- missing months, duplicate observation dates within any source even across different artifact hashes, malformed numbers, an unexpected BIS key, or a non-finite normalized value raise `ValueError`;
- v1 configuration accepts exactly the five required source IDs in their frozen order; later sources require explicit parser, normalizer, and validation support before configuration accepts them.

```python
def test_dgs10_stays_in_published_percentage_points(self):
    rows = self.fred_rows("DGS10", (("2024-01-02", "4.20"), ("2024-01-03", "4.30")), source_scale="raw")
    table = normalize_covariates(self.config, (date(2024, 1, 31),), self.rows_with(dgs10=rows))
    column = table.names.index("US_TREASURY_10Y")
    self.assertAlmostEqual(float(table.values[column, 0]), 4.25)

def test_bis_release_is_not_carried_before_four_month_lag(self):
    rows = parse_bis_gli(self.bis_artifact("2023-Q4", "5.6"))
    with self.assertRaisesRegex(ValueError, "BIS.*unavailable"):
        normalize_covariates(self.config, (date(2024, 3, 31),), self.rows_with(bis=rows))
```

- [ ] **Step 2: Run the covariate tests and verify the new functions are absent**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosCovariateTests -v`

Expected: `ERROR` naming `parse_ecb_dfr`, `parse_bis_gli`, or `normalize_covariates`.

- [ ] **Step 3: Implement ECB and BIS parsers with standard-library readers**

For ECB, require the exact configured series key in `KEY`, plus unique `TIME_PERIOD` and finite `OBS_VALUE`. Set `available_at` to the effective date at 00:00 UTC and preserve percentage points.

For BIS, open the single member `WS_GLI_csv_flat.csv` with `zipfile.ZipFile`, require these headers exactly once: `FREQ:Frequency`, `CURR_DENOM:Currency of denomination`, `BORROWERS_CTY:Borrowers' country`, `BORROWERS_SECTOR:Borrowers' sector`, `LENDERS_SECTOR:Lending sector`, `L_POS_TYPE:Position type`, `L_INSTR:Type of instruments`, `UNIT_MEASURE:Unit of measure`, `TIME_PERIOD:Time period or range`, `OBS_VALUE:Observation Value`, and `TITLE:Title`. Select only the dimensions `Q,USD,3P,N,A,I,B` and unit code `771`; require quarterly periods in `YYYY-Qn` form and finite percentage values.

```python
def _month_end(value: date) -> date:
    following = date(value.year + (value.month == 12), value.month % 12 + 1, 1)
    return following - timedelta(days=1)

def _quarter_end(period: str) -> date:
    year_text, quarter_text = period.split("-Q")
    month = int(quarter_text) * 3
    return _month_end(date(int(year_text), month, 1))

def _bis_available_at(period_end: date) -> datetime:
    month_index = period_end.year * 12 + period_end.month - 1 + 4
    year, month_zero = divmod(month_index, 12)
    available = _month_end(date(year, month_zero + 1, 1))
    return datetime(available.year, available.month, available.day, 23, 59, 59, tzinfo=timezone.utc)
```

- [ ] **Step 4: Implement monthly point-in-time transformations**

Build every monthly value using only rows with `available_at <= month_end 23:59:59 UTC`. Call the existing FRED parser for DGS10 with `series_id="DGS10"`, `source_scale="raw"`, `unit="percent_per_annum"`, `frequency="daily"`, and `availability="treasury_next_business_day"`. Call it for Brent with `source_scale="raw"` and replace each parsed row's availability with observation date plus seven calendar days before computing monthly means and log returns. Do not forward-fill Treasury or Brent. Forward-fill only ECB effective rates and already-released BIS quarters.

```python
REQUIRED_V1_COVARIATES = ("ECB_DFR", "US_TREASURY_10Y", "BRENT_RETURN", "BIS_USD_CREDIT_YOY", "US_CPI_YOY")

def covariate_names(config: ChronosConfig) -> tuple[str, ...]:
    return tuple(source.source_id for source in config.sources)

def _eligible(rows: Iterable[ObservationRow], cutoff: datetime) -> tuple[ObservationRow, ...]:
    return tuple(row for row in validate_rows(rows) if row.available_at <= cutoff)

def _daily_month_mean(rows: Iterable[ObservationRow], month: date, cutoff: datetime) -> float:
    values = [float(row.value) for row in _eligible(rows, cutoff) if _month_end(row.observation_date) == month]
    if not values:
        raise ValueError(f"daily series is unavailable for {month.isoformat()}")
    return math.fsum(values) / len(values)
```

- [ ] **Step 5: Run the covariate tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosCovariateTests -v`

Expected: all `ChronosCovariateTests` pass.

- [ ] **Step 6: Record the checkpoint**

If Git is available, run `git add perpetual_engine/chronos_data.py tests/test_chronos.py` and `git commit -m "feat: normalize Chronos covariates"`. Otherwise continue without initializing Git.

---
