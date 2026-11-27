"""Versioned report exporters: JSON, Markdown, CSV, and print-ready self-contained HTML."""

from __future__ import annotations

import csv
import html
import io
import json
from typing import Any, Dict, List, Optional, Sequence

from fund_reporting.domain.models import ReportRecord


def _fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "N/A (flagged)"
    return f"{val * 100.0:.2f}%"


def _fmt_num(val: Optional[float], digits: int = 2) -> str:
    if val is None:
        return "N/A (flagged)"
    return f"{val:,.{digits}f}"


def export_report_json(report: ReportRecord) -> str:
    """Export canonical versioned JSON including immutable ID, SHA-256, disclosures, and payload."""
    envelope = {
        "version_id": report.version_id,
        "report_series_id": report.report_series_id,
        "revision_number": report.revision_number,
        "supersedes_version_id": report.supersedes_version_id,
        "revision_reason": report.revision_reason,
        "status": report.status.value,
        "audience": report.audience.value,
        "environment": report.environment.value,
        "data_status": report.data_status.value,
        "methodology_version": report.methodology_version,
        "as_of_utc": report.as_of_utc,
        "published_at_utc": report.published_at_utc,
        "created_by": report.created_by,
        "reviewed_by": report.reviewed_by,
        "approved_by": report.approved_by,
        "content_sha256": report.content_sha256,
        "payload": report.payload,
    }
    return json.dumps(envelope, indent=2, sort_keys=True)


def export_report_csv(report: ReportRecord) -> str:
    """Export daily reconciled NAV, cash flows, and gross/net returns as CSV with provenance header."""
    buf = io.StringIO()
    disc = report.payload.get("disclosure", {})
    buf.write(f"# version_id: {report.version_id}\n")
    buf.write(f"# content_sha256: {report.content_sha256}\n")
    buf.write(f"# environment: {report.environment.value} | data_status: {report.data_status.value}\n")
    buf.write(f"# disclosure: {disc.get('banner', '')}\n")
    buf.write(f"# methodology: {report.methodology_version} | currency: {report.reporting_currency}\n")

    writer = csv.writer(buf)
    writer.writerow(
        [
            "valuation_date",
            "currency",
            "opening_nav",
            "net_cash_flow",
            "effective_capital",
            "gross_pnl",
            "total_fees",
            "net_pnl",
            "closing_nav",
            "gross_return",
            "net_return",
            "cumulative_gross_return",
            "cumulative_net_return",
        ]
    )
    for pt in report.payload.get("daily_series", []):
        writer.writerow(
            [
                pt["valuation_date"],
                pt["currency"],
                pt["opening_nav"],
                pt["net_cash_flow"],
                pt["effective_capital"],
                pt["gross_pnl"],
                pt["total_fees"],
                pt["net_pnl"],
                pt["closing_nav"],
                f"{pt['gross_return']:.8f}",
                f"{pt['net_return']:.8f}",
                f"{pt['cumulative_gross_return']:.8f}",
                f"{pt['cumulative_net_return']:.8f}",
            ]
        )
    return buf.getvalue()


def export_report_markdown(report: ReportRecord) -> str:
    """Export human-readable Markdown report with mandatory disclosures and reconciliation evidence."""
    p = report.payload
    fund = p["fund"]
    disc = p["disclosure"]
    recon = p["reconciliation_summary"]
    gross = p["metrics"]["gross"]
    net = p["metrics"]["net"]
    bm = p.get("benchmark", {})
    attr = p.get("attribution", {})
    flags = p.get("flags", [])

    lines: List[str] = [
        f"# {fund['name']} — Performance & Risk Report",
        "",
        f"> **{disc['banner']}**",
        "",
        "| Metadata | Value |",
        "|---|---|",
        f"| Immutable Version ID | `{report.version_id}` |",
        f"| Content SHA-256 | `{report.content_sha256}` |",
        f"| Series / Revision | `{report.report_series_id}` (rev {report.revision_number}) |",
        f"| Supersedes | `{report.supersedes_version_id or 'None'}` |",
        f"| Revision Reason | {report.revision_reason or 'Initial release'} |",
        f"| Status / Audience | `{report.status.value}` / `{report.audience.value}` |",
        f"| Environment / Data Status | `{report.environment.value}` / `{report.data_status.value}` |",
        f"| Period | `{report.period_start}` to `{report.period_end}` |",
        f"| As-Of UTC | `{report.as_of_utc}` |",
        f"| Reporting Currency | `{report.reporting_currency}` (Fund base: `{fund['base_currency']}`) |",
        f"| Methodology | `{report.methodology_version}` |",
        f"| Approval Chain | Author: `{report.created_by}` / Reviewer: `{report.reviewed_by or 'Pending'}` / Approver: `{report.approved_by or 'Pending'}` |",
        "",
        "## Performance Summary (Gross vs. Net)",
        "",
        "| Metric | Gross | Net |",
        "|---|---:|---:|",
        f"| Cumulative TWR | {_fmt_pct(gross.get('cumulative_return'))} | {_fmt_pct(net.get('cumulative_return'))} |",
        f"| Modified Dietz | {_fmt_pct(gross.get('modified_dietz_return'))} | {_fmt_pct(net.get('modified_dietz_return'))} |",
        f"| Annualized Return (CAGR) | {_fmt_pct(gross.get('annualized_return'))} | {_fmt_pct(net.get('annualized_return'))} |",
        f"| Annualized Volatility | {_fmt_pct(gross.get('annualized_volatility'))} | {_fmt_pct(net.get('annualized_volatility'))} |",
        f"| Sharpe Ratio | {_fmt_num(gross.get('sharpe_ratio'))} | {_fmt_num(net.get('sharpe_ratio'))} |",
        f"| Sortino Ratio | {_fmt_num(gross.get('sortino_ratio'))} | {_fmt_num(net.get('sortino_ratio'))} |",
        f"| Max Drawdown | {_fmt_pct(gross.get('max_drawdown'))} | {_fmt_pct(net.get('max_drawdown'))} |",
        f"| Calmar Ratio | {_fmt_num(gross.get('calmar_ratio'))} | {_fmt_num(net.get('calmar_ratio'))} |",
        f"| Historical Daily VaR (95%) | {_fmt_pct(gross.get('var_95_daily'))} | {_fmt_pct(net.get('var_95_daily'))} |",
        f"| Historical Daily CVaR (95%) | {_fmt_pct(gross.get('cvar_95_daily'))} | {_fmt_pct(net.get('cvar_95_daily'))} |",
        "",
        "## Ledger Reconciliation Bridge",
        "",
        f"- Opening NAV: `{_fmt_num(recon['opening_nav'])} {report.reporting_currency}`",
        f"- Subscriptions / Redemptions: `+{_fmt_num(recon['total_subscriptions'])}` / `-{_fmt_num(recon['total_redemptions'])}` (Net CF: `{_fmt_num(recon['net_cash_flows'])}`)",
        f"- Gross P&L / Total Fees / Net P&L: `{_fmt_num(recon['total_gross_pnl'])}` / `{_fmt_num(recon['total_fees'])}` / `{_fmt_num(recon['total_net_pnl'])}`",
        f"- Closing NAV: `{_fmt_num(recon['closing_nav'])} {report.reporting_currency}` (Bridge Residual: `{recon['nav_bridge_residual']}`)",
        f"- All Snapshots Reconciled: `{recon['all_snapshots_reconciled']}` (`{recon['snapshot_count']}` snapshots)",
        "",
    ]

    if bm.get("available"):
        lines.extend(
            [
                f"## Benchmark Comparison (`{bm['benchmark_id']}`)",
                "",
                f"- Benchmark Cumulative Return: {_fmt_pct(bm.get('benchmark_cumulative_return'))}",
                f"- Active Return (Net vs. Benchmark): {_fmt_pct(bm.get('active_cumulative_return'))}",
                f"- Tracking Error (Ann.): {_fmt_pct(bm.get('tracking_error_annualized'))}",
                f"- Information Ratio: {_fmt_num(bm.get('information_ratio'))}",
                f"- Beta / Ann. Alpha: {_fmt_num(bm.get('beta'))} / {_fmt_pct(bm.get('alpha_annualized'))}",
                "",
            ]
        )

    strategies = attr.get("strategies", [])
    if strategies:
        lines.extend(
            [
                "## Strategy Attribution (Carino-Linked)",
                "",
                "| Strategy | Asset Class | Avg Weight | Gross P&L | Linked Contrib | TCA Costs |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for st in strategies:
            lines.append(
                f"| {st['strategy_name']} | {st['asset_class']} | {_fmt_pct(st['average_weight'])} | "
                f"{_fmt_num(st['total_gross_pnl'])} | {_fmt_pct(st['linked_gross_contribution'])} | "
                f"{_fmt_num(st['total_transaction_costs'])} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Disclosures & Metric Quality Flags",
            "",
            f"- **Fee Basis**: {disc['fee_disclosure']}",
            f"- **Currency Basis**: {disc['currency_disclosure']}",
            f"- **Audit Status**: {disc['audit_disclosure']}",
            "",
        ]
    )
    for f in flags:
        lines.append(f"- `[{f['severity']}] {f['code']}` ({f['metric']}): {f['message']}")

    return "\n".join(lines) + "\n"


def _svg_polyline(values: Sequence[float], width: int = 640, height: int = 160, stroke: str = "#1d4ed8") -> str:
    if len(values) < 2:
        return f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}"><text x="16" y="80" fill="#64748b">Insufficient series points</text></svg>'
    lo, hi = min(values), max(values)
    span = hi - lo if abs(hi - lo) > 1e-12 else 1.0
    pad = 16
    pts = []
    for idx, v in enumerate(values):
        x = pad + (idx / (len(values) - 1)) * (width - 2 * pad)
        y = height - pad - ((v - lo) / span) * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px">'
        f'<polyline fill="none" stroke="{stroke}" stroke-width="2.2" points="{" ".join(pts)}" />'
        f"</svg>"
    )


def export_report_html(report: ReportRecord) -> str:
    """Export print-ready self-contained HTML report with inline charts and prominent disclosures."""
    p = report.payload
    fund = p["fund"]
    disc = p["disclosure"]
    recon = p["reconciliation_summary"]
    gross = p["metrics"]["gross"]
    net = p["metrics"]["net"]
    strategies = p.get("attribution", {}).get("strategies", [])
    flags = p.get("flags", [])
    daily = p.get("daily_series", [])

    wealth_svg = _svg_polyline([d["net_wealth_index"] for d in daily], stroke="#0f766e")
    dd_svg = _svg_polyline([d["drawdown"] for d in net.get("drawdown_series", [])], stroke="#dc2626")

    banner_bg = "#fef2f2" if disc["is_simulated_or_paper"] else "#eff6ff"
    banner_border = "#dc2626" if disc["is_simulated_or_paper"] else "#2563eb"
    banner_color = "#991b1b" if disc["is_simulated_or_paper"] else "#1e40af"

    strat_rows = "".join(
        f"<tr><td>{html.escape(s['strategy_name'])}</td><td>{html.escape(s['asset_class'])}</td>"
        f"<td class='num'>{_fmt_pct(s['average_weight'])}</td>"
        f"<td class='num'>{_fmt_num(s['total_gross_pnl'])}</td>"
        f"<td class='num'>{_fmt_pct(s['linked_gross_contribution'])}</td>"
        f"<td class='num'>{_fmt_num(s['total_transaction_costs'])}</td></tr>"
        for s in strategies
    )
    flag_items = "".join(
        f"<li><strong>[{html.escape(f['severity'])}] {html.escape(f['code'])}</strong> "
        f"({html.escape(f['metric'])}): {html.escape(f['message'])}</li>"
        for f in flags
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{html.escape(fund['name'])} — {html.escape(report.version_id)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; color: #0f172a; max-width: 960px; margin: 24px auto; padding: 0 20px; line-height: 1.5; }}
  .banner {{ background: {banner_bg}; border: 2px solid {banner_border}; color: {banner_color}; padding: 12px 16px; border-radius: 8px; font-weight: 700; margin-bottom: 20px; }}
  .meta-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; font-size: 13px; margin-bottom: 20px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }}
  .kpi {{ border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; background: #fff; }}
  .kpi .label {{ font-size: 12px; color: #475569; text-transform: uppercase; }}
  .kpi .val {{ font-size: 20px; font-weight: 700; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0 24px; font-size: 14px; }}
  th, td {{ border-bottom: 1px solid #e2e8f0; padding: 8px 10px; text-align: left; }}
  th {{ background: #f1f5f9; font-weight: 600; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .footer {{ margin-top: 28px; padding-top: 12px; border-top: 1px solid #cbd5e1; font-size: 12px; color: #475569; }}
  code {{ background: #f1f5f9; padding: 2px 5px; border-radius: 4px; font-size: 12px; }}
</style>
</head>
<body>
  <div class="banner">{html.escape(disc['banner'])}</div>
  <h1>{html.escape(fund['name'])} — {html.escape(report.audience.value)} Report</h1>
  <div class="meta-grid">
    <div><strong>Version ID:</strong><br/><code>{html.escape(report.version_id)}</code></div>
    <div><strong>Period:</strong><br/>{html.escape(report.period_start)} to {html.escape(report.period_end)}</div>
    <div><strong>Status / Env:</strong><br/>{html.escape(report.status.value)} / {html.escape(report.environment.value)}</div>
    <div><strong>Currency:</strong><br/>{html.escape(report.reporting_currency)} (Base: {html.escape(fund['base_currency'])})</div>
    <div><strong>Methodology:</strong><br/><code>{html.escape(report.methodology_version)}</code></div>
    <div><strong>As-Of UTC:</strong><br/><code>{html.escape(report.as_of_utc)}</code></div>
  </div>
  <div class="kpi-grid">
    <div class="kpi"><div class="label">Net TWR</div><div class="val">{_fmt_pct(net.get('cumulative_return'))}</div></div>
    <div class="kpi"><div class="label">Gross TWR</div><div class="val">{_fmt_pct(gross.get('cumulative_return'))}</div></div>
    <div class="kpi"><div class="label">Net Sharpe</div><div class="val">{_fmt_num(net.get('sharpe_ratio'))}</div></div>
    <div class="kpi"><div class="label">Max Drawdown</div><div class="val">{_fmt_pct(net.get('max_drawdown'))}</div></div>
  </div>
  <h2>Net Wealth Index</h2>
  {wealth_svg}
  <h2>Underwater Drawdown</h2>
  {dd_svg}
  <h2>Gross vs. Net Performance & Risk</h2>
  <table>
    <thead><tr><th>Metric</th><th class="num">Gross</th><th class="num">Net</th></tr></thead>
    <tbody>
      <tr><td>Cumulative TWR</td><td class="num">{_fmt_pct(gross.get('cumulative_return'))}</td><td class="num">{_fmt_pct(net.get('cumulative_return'))}</td></tr>
      <tr><td>Annualized Return (CAGR)</td><td class="num">{_fmt_pct(gross.get('annualized_return'))}</td><td class="num">{_fmt_pct(net.get('annualized_return'))}</td></tr>
      <tr><td>Annualized Volatility</td><td class="num">{_fmt_pct(gross.get('annualized_volatility'))}</td><td class="num">{_fmt_pct(net.get('annualized_volatility'))}</td></tr>
      <tr><td>Sharpe Ratio</td><td class="num">{_fmt_num(gross.get('sharpe_ratio'))}</td><td class="num">{_fmt_num(net.get('sharpe_ratio'))}</td></tr>
      <tr><td>Sortino Ratio</td><td class="num">{_fmt_num(gross.get('sortino_ratio'))}</td><td class="num">{_fmt_num(net.get('sortino_ratio'))}</td></tr>
      <tr><td>Max Drawdown</td><td class="num">{_fmt_pct(gross.get('max_drawdown'))}</td><td class="num">{_fmt_pct(net.get('max_drawdown'))}</td></tr>
    </tbody>
  </table>
  <h2>Strategy Attribution</h2>
  <table>
    <thead><tr><th>Strategy</th><th>Asset Class</th><th class="num">Avg Weight</th><th class="num">Gross P&amp;L</th><th class="num">Linked Contrib</th><th class="num">TCA Costs</th></tr></thead>
    <tbody>{strat_rows or '<tr><td colspan="6">No strategy breakdown provided.</td></tr>'}</tbody>
  </table>
  <h2>Disclosures &amp; Quality Flags</h2>
  <p><strong>Fee Basis:</strong> {html.escape(disc['fee_disclosure'])}</p>
  <p><strong>Currency Basis:</strong> {html.escape(disc['currency_disclosure'])}</p>
  <p><strong>Audit &amp; Reconciliation:</strong> {html.escape(disc['audit_disclosure'])} (Closing NAV: {_fmt_num(recon['closing_nav'])} {html.escape(report.reporting_currency)}, Bridge Residual: {recon['nav_bridge_residual']})</p>
  <ul>{flag_items}</ul>
  <div class="footer">
    Immutable SHA-256 Digest: <code>{html.escape(report.content_sha256)}</code> |
    Author: <code>{html.escape(report.created_by)}</code> |
    Reviewer: <code>{html.escape(report.reviewed_by or 'Pending')}</code> |
    Approver: <code>{html.escape(report.approved_by or 'Pending')}</code> |
    Supersedes: <code>{html.escape(report.supersedes_version_id or 'None')}</code>
  </div>
</body>
</html>
"""
