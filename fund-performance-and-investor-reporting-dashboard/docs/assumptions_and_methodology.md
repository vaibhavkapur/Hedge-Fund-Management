# Assumptions, Metric Conventions, and Methodology

**Methodology Version:** `SAAT-PERF-METH-1.0.0` (versioned in `config/default_config.json`)

## 1. Time, Currency, and Ledger Conventions

- **Timestamps:** All `as_of_utc`, `availability_utc`, `created_at_utc`, and `published_at_utc` fields are strict UTC ISO-8601 (`Z` or `+00:00`). Point-in-time queries filter snapshots by `availability_utc <= as_of_utc` to prevent look-ahead bias.
- **Currency Units:** Explicit 3-letter ISO-4217 codes (`USD`, `EUR`, `GBP`, etc.). Conversions use point-in-time FX rates (`base_currency -> quote_currency` or exact inverse). Missing FX dates fail closed with `MISSING_FX_RATE`.
- **Ledger Reconciliation Bridge:** Every `NavSnapshot` must satisfy:
  $$\text{closing\_nav} = \text{opening\_nav} + (\text{subscriptions} - \text{redemptions}) + \text{gross\_pnl} - (\text{management\_fee} + \text{performance\_fee} + \text{other\_expenses})$$
  within `max(0.01, 1e-6 * expected_closing_nav)`. Consecutive valuation dates must roll forward (`opening_nav_t == closing_nav_{t-1}` in base currency) with no calendar gap exceeding 5 days unless explicitly configured.

## 2. Gross vs. Net Fee Conventions

- **Gross basis:** Computed from `gross_pnl` before management fee, performance fee, and other fund expenses (trading/execution transaction costs are tracked separately via strategy TCA).
- **Net basis:** Computed from `net_pnl = gross_pnl - (management_fee + performance_fee + other_expenses)`.
- Default synthetic schedule (`config/default_config.json`): 150 bps/year management fee accrued on a 252-day basis, 20% performance fee above high-water mark and annual hurdle rate.

## 3. Return Formulas

### Daily Sub-Period Time-Weighted Return (TWR)
- For `START_OF_DAY` external cash flows:
  $$V_{\text{eff}, t} = \text{opening\_nav}_t + \text{subscriptions}_t - \text{redemptions}_t$$
  $$r_{\text{gross}, t} = \frac{\text{gross\_pnl}_t}{V_{\text{eff}, t}}, \qquad r_{\text{net}, t} = \frac{\text{net\_pnl}_t}{V_{\text{eff}, t}}$$
- For `END_OF_DAY` external cash flows:
  $$V_{\text{eff}, t} = \text{opening\_nav}_t$$
- Cumulative TWR over $T$ trading days:
  $$R_{\text{cum}} = \prod_{t=1}^{T}(1 + r_t) - 1$$
- Annualized Return (CAGR) using $D = 252$ trading days/year:
  $$R_{\text{ann}} = (1 + R_{\text{cum}})^{D/T} - 1$$

### Multi-Currency Translation
When converting from base currency $C_b$ to reporting currency $C_r$ using closing FX rates $FX_t$:
- Opening NAV is translated at prior close $FX_{t-1}$, closing NAV at $FX_t$, and cash flows at $FX_{t-1}$ (`START_OF_DAY`) or $FX_t$ (`END_OF_DAY`).
- Translated gross P&L incorporates unhedged FX translation so that $(1 + r_{t, C_r}) = (1 + r_{t, C_b}) \cdot \frac{FX_t}{FX_{t-1}}$ and the NAV bridge reconciles with zero residual in $C_r$.

### Modified Dietz (Cross-Check)
$$R_{\text{MD}} = \frac{V_{\text{end}} - V_{\text{start}} - \sum CF_i}{V_{\text{start}} + \sum w_i CF_i}$$

## 4. Risk, Drawdown, and Benchmark Metrics

- **Annualized Volatility:** Sample standard deviation (`ddof=1`) of daily returns multiplied by $\sqrt{252}$. Values $\le 10^{-12}$ are clamped to `0.0`.
- **Sharpe Ratio:** $\frac{\bar{r}_{\text{daily}} - r_f / 252}{\sigma_{\text{daily}}} \sqrt{252}$ (default $r_f = 4.0\%$). Omitted (`null`) with `ZERO_VOLATILITY` flag when $\sigma_{\text{daily}} \le 10^{-12}$.
- **Sortino Ratio:** Uses annualized downside deviation relative to daily risk-free MAR. Omitted (`null`) with `ZERO_VOLATILITY` flag when downside deviation $\le 10^{-12}$.
- **Drawdown & Calmar:** Running peak-to-trough percentage decline on cumulative wealth index, tracking exact `peak_date`, `trough_date`, `recovery_date`, and Ulcer index. Calmar = $R_{\text{ann}} / |\text{MaxDD}|$, omitted with `ZERO_VOLATILITY` when MaxDD is zero.
- **Historical VaR / CVaR (95%):** Lower 5th-percentile daily return and tail mean.
- **Benchmark Metrics:** Active return, annualized tracking error, information ratio, OLS beta, annualized Jensen's alpha, correlation, and up/down capture ratios.

## 5. Strategy Attribution (Carino Linking)

Daily strategy contribution $c_{s,t} = \frac{\text{gross\_pnl}_{s,t}}{V_{\text{eff},t}}$ is linked across periods via Carino logarithmic factors $k_t = \frac{\ln(1+r_t)}{r_t}$ and $K = \frac{\ln(1+R_{\text{cum}})}{R_{\text{cum}}}$:
$$C_s = \sum_{t=1}^{T} \frac{k_t}{K} c_{s,t}, \qquad \sum_s C_s \equiv R_{\text{cum}}$$

## 6. Minimum History Thresholds & Quality Flags

Configured in `config/default_config.json`:
- Annualized return / volatility: `< 20` observations -> `INADEQUATE_HISTORY`
- Beta / alpha / VaR / CVaR: `< 30` observations -> `INADEQUATE_HISTORY`
- Sharpe / Sortino / Calmar: `< 60` observations -> `INADEQUATE_HISTORY`
- Non-live data always emits `SIMULATED_DATA` or `PAPER_DATA` and `UNAUDITED_DATA`.
