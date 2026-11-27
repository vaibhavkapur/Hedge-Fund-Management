(() => {
  const els = {
    banner: document.getElementById("disclosure-banner"),
    fundMeta: document.getElementById("fund-meta"),
    roleSelect: document.getElementById("role-select"),
    fundSelect: document.getElementById("fund-select"),
    currencySelect: document.getElementById("currency-select"),
    basisSelect: document.getElementById("basis-select"),
    refreshBtn: document.getElementById("refresh-btn"),
    alertBox: document.getElementById("alert-box"),
    analyticsSection: document.getElementById("analytics-section"),
    kpiGrid: document.getElementById("kpi-grid"),
    wealthChart: document.getElementById("wealth-chart"),
    drawdownChart: document.getElementById("drawdown-chart"),
    attributionChart: document.getElementById("attribution-chart"),
    attributionTable: document.getElementById("attribution-table"),
    exposureChart: document.getElementById("exposure-chart"),
    exposureTable: document.getElementById("exposure-table"),
    diagnosticsBox: document.getElementById("diagnostics-box"),
    disclosuresBox: document.getElementById("disclosures-box"),
    flagsList: document.getElementById("flags-list"),
    methodologyBadge: document.getElementById("methodology-badge"),
    ddBadge: document.getElementById("dd-badge"),
    tcaBadge: document.getElementById("tca-badge"),
    exposureBadge: document.getElementById("exposure-badge"),
    reconBadge: document.getElementById("recon-badge"),
    flagCountBadge: document.getElementById("flag-count-badge"),
    seriesInput: document.getElementById("series-input"),
    audienceSelect: document.getElementById("audience-select"),
    createReportBtn: document.getElementById("create-report-btn"),
    reportsTable: document.getElementById("reports-table"),
    auditTable: document.getElementById("audit-table"),
    refreshAuditBtn: document.getElementById("refresh-audit-btn"),
  };

  function token() {
    return els.roleSelect.value;
  }

  function showAlert(msg, kind = "info") {
    els.alertBox.textContent = msg;
    els.alertBox.className = `alert ${kind}`;
  }
  function clearAlert() {
    els.alertBox.className = "alert hidden";
  }

  async function apiFetch(path, options = {}) {
    const headers = Object.assign(
      { Authorization: `Bearer ${token()}`, "Content-Type": "application/json" },
      options.headers || {}
    );
    const res = await fetch(path, Object.assign({}, options, { headers }));
    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch { data = text; }
    if (!res.ok) {
      const errMsg = data?.error ? `[${res.status} ${data.error.code}] ${data.error.message}` : `HTTP ${res.status}`;
      throw new Error(errMsg);
    }
    return data;
  }

  const fmtPct = (v) => (v === null || v === undefined ? "N/A" : `${(v * 100).toFixed(2)}%`);
  const fmtNum = (v, d = 2) => (v === null || v === undefined ? "N/A" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));

  function renderLineSvg(seriesList, width = 580, height = 185) {
    const allVals = seriesList.flatMap((s) => s.values);
    if (allVals.length < 2) return "<p>Insufficient data points.</p>";
    const lo = Math.min(...allVals);
    const hi = Math.max(...allVals);
    const span = Math.abs(hi - lo) > 1e-12 ? hi - lo : 1;
    const pad = 22;
    const polylines = seriesList.map((s) => {
      const pts = s.values.map((v, i) => {
        const x = pad + (i / Math.max(1, s.values.length - 1)) * (width - pad * 2);
        const y = height - pad - ((v - lo) / span) * (height - pad * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(" ");
      return `<polyline fill="none" stroke="${s.color}" stroke-width="2.2" points="${pts}" />`;
    }).join("");
    const legend = seriesList.map((s, idx) =>
      `<g transform="translate(${pad + idx * 145}, 14)">
         <rect width="12" height="3" y="-4" fill="${s.color}" />
         <text x="16" y="0" font-size="11" fill="#334155">${s.label}</text>
       </g>`
    ).join("");
    return `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px">
      ${legend}${polylines}
      <text x="${width - pad}" y="${height - 6}" font-size="10" text-anchor="end" fill="#64748b">Min: ${lo.toFixed(3)} | Max: ${hi.toFixed(3)}</text>
    </svg>`;
  }

  function renderBarSvg(items, width = 560, height = 150) {
    if (!items.length) return "<p>No strategy data.</p>";
    const maxAbs = Math.max(...items.map((x) => Math.abs(x.value)), 0.0001);
    const rowH = Math.floor((height - 16) / items.length);
    const midX = 220;
    const maxW = width - midX - 65;
    const bars = items.map((item, i) => {
      const y = 10 + i * rowH;
      const w = (Math.abs(item.value) / maxAbs) * maxW;
      const x = item.value >= 0 ? midX : midX - w;
      const color = item.value >= 0 ? "#0f766e" : "#dc2626";
      return `<text x="6" y="${y + rowH * 0.65}" font-size="11" fill="#1e293b">${item.label}</text>
        <rect x="${x.toFixed(1)}" y="${y + 4}" width="${Math.max(2, w).toFixed(1)}" height="${Math.max(10, rowH - 10)}" fill="${color}" rx="3" />
        <text x="${width - 6}" y="${y + rowH * 0.65}" font-size="11" text-anchor="end" fill="#334155">${fmtPct(item.value)}</text>`;
    }).join("");
    return `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px">
      <line x1="${midX}" y1="6" x2="${midX}" y2="${height - 6}" stroke="#cbd5e1" stroke-dasharray="3,3" />
      ${bars}
    </svg>`;
  }

  async function loadFunds() {
    const data = await apiFetch("/api/v1/funds");
    els.fundSelect.innerHTML = data.funds.map((f) => `<option value="${f.fund_id}">${f.name} (${f.fund_id})</option>`).join("");
  }

  async function loadAnalytics() {
    const fundId = els.fundSelect.value;
    if (!fundId) return;
    const ccy = els.currencySelect.value;
    const basis = els.basisSelect.value;

    try {
      const data = await apiFetch(`/api/v1/funds/${encodeURIComponent(fundId)}/analytics?currency=${encodeURIComponent(ccy)}`);
      els.analyticsSection.classList.remove("hidden");

      const disc = data.disclosure;
      els.banner.textContent = disc.banner;
      els.banner.className = `disclosure-banner ${disc.is_simulated_or_paper ? "simulated" : "live"}`;
      els.fundMeta.textContent = `${data.fund.name} | Period: ${data.period_start} to ${data.period_end} | As-Of UTC: ${data.as_of_utc}`;

      const m = data.metrics[basis];
      const recon = data.reconciliation_summary;
      els.kpiGrid.innerHTML = [
        { label: `${basis.toUpperCase()} Cumulative TWR`, val: fmtPct(m.cumulative_return), sub: `Mod. Dietz: ${fmtPct(m.modified_dietz_return)}` },
        { label: `${basis.toUpperCase()} Ann. Return (CAGR)`, val: fmtPct(m.annualized_return), sub: `Ann. Vol: ${fmtPct(m.annualized_volatility)}` },
        { label: `${basis.toUpperCase()} Sharpe / Sortino`, val: `${fmtNum(m.sharpe_ratio)} / ${fmtNum(m.sortino_ratio)}`, sub: `Calmar: ${fmtNum(m.calmar_ratio)}` },
        { label: "Max Drawdown", val: fmtPct(m.max_drawdown), sub: `Trough: ${m.max_drawdown_trough_date || "None"}` },
        { label: `Closing NAV (${data.reporting_currency})`, val: fmtNum(recon.closing_nav, 0), sub: `Net CF: ${fmtNum(recon.net_cash_flows, 0)}` },
        { label: "Fee Drag (Gross - Net)", val: fmtPct(data.metrics.fee_drag_cumulative), sub: `Total Fees: ${fmtNum(recon.total_fees, 0)}` },
      ].map((k) => `<div class="kpi-card"><div class="kpi-label">${k.label}</div><div class="kpi-val">${k.val}</div><div class="kpi-sub">${k.sub}</div></div>`).join("");

      els.methodologyBadge.textContent = data.methodology_version;
      const bmSeries = data.benchmark?.available ? data.benchmark.series.map((b) => b.wealth_index) : [];
      const wealthSeries = [
        { label: "Net Wealth", color: "#0f766e", values: data.daily_series.map((d) => d.net_wealth_index) },
        { label: "Gross Wealth", color: "#2563eb", values: data.daily_series.map((d) => d.gross_wealth_index) },
      ];
      if (bmSeries.length) wealthSeries.push({ label: data.benchmark.benchmark_id, color: "#94a3b8", values: bmSeries });
      els.wealthChart.innerHTML = renderLineSvg(wealthSeries);

      els.ddBadge.textContent = `Current DD: ${fmtPct(m.current_drawdown)}`;
      els.drawdownChart.innerHTML = renderLineSvg([
        { label: `${basis.toUpperCase()} Drawdown`, color: "#dc2626", values: m.drawdown_series.map((d) => d.drawdown) },
      ]);

      const strats = data.attribution.strategies || [];
      els.tcaBadge.textContent = `TCA: ${fmtNum(data.attribution.total_transaction_costs, 0)} ${data.reporting_currency}`;
      els.attributionChart.innerHTML = renderBarSvg(strats.map((s) => ({ label: s.strategy_name, value: s.linked_gross_contribution })));
      els.attributionTable.innerHTML = `<thead><tr><th>Strategy</th><th>Class</th><th class="num">Avg Wt</th><th class="num">Gross PnL</th><th class="num">Contrib</th><th class="num">TCA</th></tr></thead><tbody>` +
        strats.map((s) => `<tr><td>${s.strategy_name}</td><td>${s.asset_class}</td><td class="num">${fmtPct(s.average_weight)}</td><td class="num">${fmtNum(s.total_gross_pnl, 0)}</td><td class="num">${fmtPct(s.linked_gross_contribution)}</td><td class="num">${fmtNum(s.total_transaction_costs, 0)}</td></tr>`).join("") + `</tbody>`;

      const expTs = data.exposure.time_series || [];
      const latestExp = data.exposure.latest || {};
      els.exposureBadge.textContent = `Gross ${fmtPct(latestExp.gross_exposure_pct_nav)} | Net ${fmtPct(latestExp.net_exposure_pct_nav)} | Cash ${fmtPct(latestExp.cash_pct_nav)}`;
      els.exposureChart.innerHTML = renderLineSvg([
        { label: "Gross % NAV", color: "#1d4ed8", values: expTs.map((x) => x.gross_exposure_pct_nav) },
        { label: "Net % NAV", color: "#0f766e", values: expTs.map((x) => x.net_exposure_pct_nav) },
        { label: "Cash % NAV", color: "#d97706", values: expTs.map((x) => x.cash_pct_nav) },
      ]);
      els.exposureTable.innerHTML = `<thead><tr><th>Asset Class</th><th class="num">Weight</th><th class="num">Long</th><th class="num">Short</th><th class="num">Gross %</th><th class="num">Net %</th></tr></thead><tbody>` +
        (data.exposure.by_asset_class || []).map((a) => `<tr><td>${a.asset_class}</td><td class="num">${fmtPct(a.weight)}</td><td class="num">${fmtNum(a.long_exposure, 0)}</td><td class="num">${fmtNum(a.short_exposure, 0)}</td><td class="num">${fmtPct(a.gross_pct_nav)}</td><td class="num">${fmtPct(a.net_pct_nav)}</td></tr>`).join("") + `</tbody>`;

      const diag = data.foundation_diagnostics;
      els.reconBadge.textContent = recon.all_snapshots_reconciled ? "RECONCILED" : "UNRECONCILED";
      els.reconBadge.className = `badge ${recon.all_snapshots_reconciled ? "ok" : "warn"}`;
      els.diagnosticsBox.innerHTML = `
        <p><strong>NAV Bridge Residual:</strong> <code>${recon.nav_bridge_residual}</code> across ${recon.snapshot_count} snapshots (Latest Hash: <code>${recon.latest_ledger_hash.slice(0, 16)}...</code>)</p>
        <p><strong>Risk Engine Adapter:</strong> Parametric VaR(95%): <code>${fmtPct(diag.risk_engine.parametric_var_95)}</code> | CVaR(95%): <code>${fmtPct(diag.risk_engine.cvar_95)}</code> | 2008 Stress: <code>${fmtPct(diag.risk_engine.stress_loss_2008)}</code></p>
        <p><strong>Portfolio Optimizer Adapter:</strong> Max Abs Weight Drift: <code>${fmtPct(diag.portfolio_optimizer.max_abs_drift)}</code></p>
        <p><strong>TCA Adapter:</strong> Slippage: <code>${diag.transaction_cost_analysis.estimated_slippage_bps} bps</code> | Commission: <code>${diag.transaction_cost_analysis.estimated_commission_bps} bps</code></p>
      `;

      els.flagCountBadge.textContent = `${data.flags.length} flag(s)`;
      els.disclosuresBox.innerHTML = `
        <p><strong>Fee Basis:</strong> ${disc.fee_disclosure}</p>
        <p><strong>Currency Basis:</strong> ${disc.currency_disclosure}</p>
        <p><strong>Audit Status:</strong> ${disc.audit_disclosure}</p>
      `;
      els.flagsList.innerHTML = data.flags.map((f) => `<li><strong>[${f.severity}] ${f.code}</strong> (${f.metric}): ${f.message}</li>`).join("");
    } catch (err) {
      els.analyticsSection.classList.add("hidden");
      showAlert(`Internal analytics access blocked for current role: ${err.message}`, "error");
    }
  }

  async function loadReports() {
    try {
      const data = await apiFetch("/api/v1/reports");
      const rows = data.reports.map((r) => `
        <tr>
          <td><code>${r.version_id}</code></td>
          <td>${r.audience}</td>
          <td><span class="badge ${r.status === "PUBLISHED" ? "ok" : ""}">${r.status}</span></td>
          <td>v${r.revision_number}</td>
          <td>${r.reporting_currency}</td>
          <td>${r.created_by} / ${r.reviewed_by || "—"} / ${r.approved_by || "—"}</td>
          <td>
            <div class="inline-actions">
              ${r.status === "DRAFT" ? `<button class="btn small" data-action="submit" data-id="${r.version_id}">Submit</button>` : ""}
              ${r.status === "SUBMITTED" ? `<button class="btn small" data-action="review" data-id="${r.version_id}">Review</button>` : ""}
              ${r.status === "REVIEWED" ? `<button class="btn small primary" data-action="publish" data-id="${r.version_id}">Approve &amp; Publish</button>` : ""}
              ${r.status === "PUBLISHED" ? `<button class="btn small" data-action="revise" data-id="${r.version_id}">Revise</button>` : ""}
              <button class="btn small" data-export="html" data-id="${r.version_id}">HTML</button>
              <button class="btn small" data-export="json" data-id="${r.version_id}">JSON</button>
              <button class="btn small" data-export="csv" data-id="${r.version_id}">CSV</button>
              <button class="btn small" data-export="markdown" data-id="${r.version_id}">MD</button>
            </div>
          </td>
        </tr>`).join("");
      els.reportsTable.innerHTML = `<thead><tr><th>Immutable Version ID</th><th>Audience</th><th>Status</th><th>Rev</th><th>CCY</th><th>Author / Reviewer / Approver</th><th>Workflow &amp; Exports</th></tr></thead><tbody>${rows || '<tr><td colspan="7">No visible reports for current role.</td></tr>'}</tbody>`;
    } catch (err) {
      showAlert(err.message, "error");
    }
  }

  async function loadAudit() {
    try {
      const data = await apiFetch("/api/v1/audit?limit=30");
      const rows = data.events.map((e) => `
        <tr>
          <td><code>${e.timestamp_utc}</code></td>
          <td>${e.actor_id} (${e.actor_role})</td>
          <td><code>${e.action}</code></td>
          <td>${e.resource_type}:<code>${e.resource_id}</code></td>
          <td><span class="badge ${e.outcome === "ALLOW" ? "ok" : "warn"}">${e.outcome}</span></td>
        </tr>`).join("");
      els.auditTable.innerHTML = `<thead><tr><th>Timestamp UTC</th><th>Actor</th><th>Action</th><th>Resource</th><th>Outcome</th></tr></thead><tbody>${rows}</tbody>`;
    } catch (err) {
      els.auditTable.innerHTML = `<tbody><tr><td>Audit log restricted: ${err.message}</td></tr></tbody>`;
    }
  }

  async function refreshAll() {
    clearAlert();
    await loadFunds();
    await loadAnalytics();
    await loadReports();
    await loadAudit();
  }

  els.refreshBtn.addEventListener("click", refreshAll);
  els.roleSelect.addEventListener("change", refreshAll);
  els.fundSelect.addEventListener("change", loadAnalytics);
  els.currencySelect.addEventListener("change", loadAnalytics);
  els.basisSelect.addEventListener("change", loadAnalytics);
  els.refreshAuditBtn.addEventListener("click", loadAudit);

  els.createReportBtn.addEventListener("click", async () => {
    clearAlert();
    try {
      const res = await apiFetch("/api/v1/reports", {
        method: "POST",
        body: JSON.stringify({
          fund_id: els.fundSelect.value,
          report_series_id: els.seriesInput.value.trim() || `RPT-${Date.now()}`,
          audience: els.audienceSelect.value,
          reporting_currency: els.currencySelect.value,
        }),
      });
      showAlert(`Created draft report ${res.version_id} (SHA-256: ${res.content_sha256.slice(0, 12)}...)`, "info");
      await loadReports();
      await loadAudit();
    } catch (err) {
      showAlert(err.message, "error");
      await loadAudit();
    }
  });

  els.reportsTable.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    clearAlert();
    const id = btn.dataset.id;
    const action = btn.dataset.action;
    const exp = btn.dataset.export;

    if (exp) {
      try {
        const res = await fetch(`/api/v1/reports/${encodeURIComponent(id)}/export?format=${exp}`, {
          headers: { Authorization: `Bearer ${token()}` },
        });
        const text = await res.text();
        if (!res.ok) throw new Error(text);
        const blob = new Blob([text], { type: res.headers.get("Content-Type") || "text/plain" });
        const url = URL.createObjectURL(blob);
        window.open(url, "_blank");
      } catch (err) {
        showAlert(`Export failed: ${err.message}`, "error");
      }
      return;
    }

    if (action) {
      try {
        const body = action === "revise" ? { revision_reason: "Scheduled post-reconciliation restatement" } : action === "review" ? { approve: true, review_notes: "Verified NAV bridge & disclosures" } : {};
        const res = await apiFetch(`/api/v1/reports/${encodeURIComponent(id)}/${action}`, {
          method: "POST",
          body: JSON.stringify(body),
        });
        showAlert(`Action '${action}' succeeded -> ${res.version_id} [${res.status}]`, "info");
        await loadReports();
        await loadAudit();
      } catch (err) {
        showAlert(err.message, "error");
        await loadAudit();
      }
    }
  });

  refreshAll();
})();
