const navButton = (view, icon, label) => `
  <button class="nav-item" data-view="${view}"><i data-lucide="${icon}"></i><span>${label}</span></button>`;

export function mountWorkspaces() {
  const nav = document.querySelector(".nav");
  const systemButton = nav.querySelector('[data-view="system"]');
  if (!nav.querySelector('[data-view="core3d"]')) {
    systemButton.insertAdjacentHTML("beforebegin", navButton("volatility", "waves", "Volatility Lab"));
    systemButton.insertAdjacentHTML("beforebegin", navButton("core3d", "orbit", "3D Core"));
    systemButton.insertAdjacentHTML("beforebegin", navButton("backtest", "flask-conical", "Backtest Lab"));
    systemButton.insertAdjacentHTML("beforebegin", navButton("whitepaper", "book-open-text", "White Paper"));
  }

  const system = document.getElementById("view-system");
  if (!document.getElementById("view-analytics")) system.before(makeView("analytics", analyticsMarkup()));
  if (!document.getElementById("view-ai")) system.before(makeView("ai", aiMarkup()));
  if (!document.getElementById("view-volatility")) system.before(makeView("volatility", volatilityMarkup()));
  if (!document.getElementById("view-core3d")) system.before(makeView("core3d", coreMarkup(), "scene-view"));
  if (!document.getElementById("view-backtest")) system.before(makeView("backtest", backtestMarkup()));
  if (!document.getElementById("view-whitepaper")) system.before(makeView("whitepaper", whitePaperMarkup()));
}

function volatilityMarkup(){
  return `<div class="volatility-shell"><aside class="panel volatility-controls"><div class="panel-head"><div><span class="eyebrow">LOCAL GLD RESEARCH</span><h2>Volatility Controls</h2></div><span class="badge blue">Advisory</span></div>
    <label>Rolling window <output id="volWindowValue">22 bars</output><input id="volWindow" type="range" min="5" max="90" value="22"></label>
    <label>Forecast horizon <output id="volHorizonValue">5 min</output><input id="volHorizon" type="range" min="1" max="30" value="5"></label>
    <label>Tail confidence <output id="volConfidenceValue">99%</output><input id="volConfidence" type="range" min="90" max="99.9" step="0.1" value="99"></label>
    <label>Risk budget <output id="volRiskValue">0.25%</output><input id="volRisk" type="range" min="0.05" max="1" step="0.05" value="0.25"></label>
    <button class="button primary wide" id="runVolatility"><i data-lucide="play"></i>Run local simulation</button><p class="control-note">Research output never changes position size or submits an order. Promotion into risk rules requires backtest and paper validation.</p></aside>
    <section class="panel volatility-network-panel"><div class="panel-head"><div><span class="eyebrow">TEMPORAL REGIME GRAPH</span><h2>Volatility Cluster Network</h2></div><span class="status-dot"></span></div><div class="volatility-network"><canvas id="volatilityNetwork"></canvas><div class="network-axis">LOW <span></span> NORMAL <span></span> HIGH <span></span> EXTREME</div></div></section>
    <aside class="panel volatility-verdict"><div class="panel-head"><h2>Tail-Risk Verdict</h2><span class="badge">Latest bars</span></div><strong id="volatilityVerdict" class="vol-verdict">WAITING</strong><p id="volatilityNarrative">Load local bars to classify the current regime.</p><dl class="data-list"><div><dt>Current volatility</dt><dd id="volCurrent">--</dd></div><div><dt>Forecast volatility</dt><dd id="volForecast">--</dd></div><div><dt>Regime percentile</dt><dd id="volPercentile">--</dd></div><div><dt>Persistence</dt><dd id="volPersistence">--</dd></div><div><dt>Historical VaR</dt><dd id="volVar">--</dd></div><div><dt>CVaR / expected shortfall</dt><dd id="volCvar">--</dd></div><div><dt>Size multiplier</dt><dd id="volMultiplier">--</dd></div><div><dt>Tail exposure</dt><dd id="volTailRisk">--</dd></div></dl></aside>
    <article class="panel volatility-chart"><div class="panel-head"><h2>Rolling Volatility and Regimes</h2><span class="badge blue">1-minute returns</span></div><canvas id="volatilityTimeline"></canvas></article>
    <article class="panel volatility-chart"><div class="panel-head"><h2>Return Distribution</h2><span class="badge">VaR tail highlighted</span></div><canvas id="returnDistribution"></canvas></article>
    <article class="panel transition-panel"><div class="panel-head"><h2>Regime Transition Matrix</h2><span class="badge">Conditional %</span></div><table><thead><tr><th>FROM / TO</th><th>LOW</th><th>NORMAL</th><th>HIGH</th><th>EXTREME</th></tr></thead><tbody id="volTransitionMatrix"></tbody></table></article></div>`;
}

function makeView(id, markup, extraClass = "") {
  const section = document.createElement("section");
  section.className = `view ${extraClass}`.trim();
  section.id = `view-${id}`;
  section.innerHTML = markup;
  return section;
}

function analyticsMarkup() {
  return `<div class="analytics-toolbar"><div><span class="eyebrow">COST-AWARE ROOT EPISODES</span><h2>Performance Analytics</h2></div><button class="button subtle" id="refreshAnalytics"><i data-lucide="refresh-cw"></i>Refresh</button></div>
  <div class="metric-grid compact analytics-metrics"><article class="metric"><span>EPISODES</span><strong id="analyticsTrades">--</strong></article><article class="metric"><span>NET P/L</span><strong id="analyticsNet">--</strong></article><article class="metric"><span>EST. COSTS</span><strong id="analyticsCosts">--</strong></article><article class="metric"><span>PROFIT FACTOR</span><strong id="analyticsPf">--</strong></article><article class="metric"><span>WIN RATE</span><strong id="analyticsWin">--</strong></article><article class="metric"><span>AVG HOLD</span><strong id="analyticsHold">--</strong></article></div>
  <div class="analytics-grid"><article class="panel chart-wide"><div class="panel-head"><h2>Cumulative Net P/L</h2><span class="badge">After costs</span></div><div class="analytics-chart"><canvas id="analyticsPnlCanvas"></canvas></div></article><article class="panel"><div class="panel-head"><h2>Outcome Mix</h2></div><div class="analytics-chart"><canvas id="analyticsOutcomeCanvas"></canvas></div></article><article class="panel"><div class="panel-head"><h2>P/L by Direction</h2></div><div class="analytics-chart"><canvas id="analyticsDirectionCanvas"></canvas></div></article><article class="panel chart-wide"><div class="panel-head"><h2>P/L by Playbook</h2></div><div class="analytics-chart"><canvas id="analyticsPlaybookCanvas"></canvas></div></article><article class="panel table-panel chart-wide"><div class="panel-head"><h2>Exit Reason Quality</h2></div><div class="table-wrap"><table><thead><tr><th>Exit reason</th><th>Trades</th><th>Win rate</th><th>Net P/L</th><th>Average</th></tr></thead><tbody id="analyticsBreakdownRows"></tbody></table></div></article></div>`;
}

function aiMarkup() {
  return `<div class="ai-provider-hero"><div><span class="eyebrow">SELECTABLE RESEARCH INTELLIGENCE</span><h2>AI Research and Training Lab</h2><p>Choose the reasoning engine. FinGPT supplies financial workflows; the execution engine keeps sole broker authority.</p></div><div class="provider-live"><span id="providerHealthDot" class="status-dot muted"></span><strong id="providerHealthText">NOT TESTED</strong></div></div>
  <div class="provider-grid" id="providerGrid"></div>
  <div class="ai-layout">
    <article class="panel ai-console"><div class="panel-head"><div><span class="eyebrow">OFFLINE RESEARCH ONLY</span><h2>Research Console</h2></div><span class="badge blue">No broker authority</span></div>
      <div class="ai-status"><div><span>ACTIVE ENGINE</span><strong id="aiProvider">--</strong></div><div><span>MODEL</span><strong id="aiModel">--</strong></div><div><span>STORED REVIEWS</span><strong id="aiReviewCount">0</strong></div></div>
      <form id="providerForm" class="provider-form"><input name="provider" type="hidden"><div class="field-row"><label>Endpoint<input name="base_url"></label><label>Model<input name="model"></label></div><label id="kimiKeyField">Kimi API key<input name="api_key" type="password" autocomplete="off" placeholder="Leave blank to preserve the local key"></label><div class="button-row"><button class="button primary" type="submit"><i data-lucide="power"></i>Activate engine</button><button class="button subtle" id="testProvider" type="button"><i data-lucide="plug-zap"></i>Test connection</button></div></form>
      <form id="aiPromptForm" class="prompt-form"><label>Research request<textarea name="query" rows="7">Analyze completed trades, costs, exit quality, missed opportunities, and propose evidence-based training improvements.</textarea></label><button class="button primary"><i data-lucide="send"></i>Run analysis</button></form><pre class="terminal ai-output" id="aiOutput">Select an engine, test it, then submit a research task.</pre>
    </article>
    <aside class="ai-side"><article class="panel fingpt-panel"><div class="panel-head"><h2>FinGPT Research Pipeline</h2><span class="badge" id="fingptStatus">CHECKING</span></div><p id="fingptRole">Financial prompts, RAG, sentiment, and data preparation.</p><dl class="data-list" id="fingptDetails"></dl></article>
      <article class="panel"><div class="panel-head"><h2>Research Tasks</h2><span class="badge blue">8 workflows</span></div><div class="research-task-grid">
        <button class="command" data-action="llm_macro"><i data-lucide="landmark"></i><span><strong>Macro context</strong><small>Gold, USD, rates and event risk</small></span></button>
        <button class="command" data-action="llm_coach"><i data-lucide="graduation-cap"></i><span><strong>RAG coach</strong><small>Knowledge, journal and outcomes</small></span></button>
        <button class="command" data-action="llm_council" data-horizon="daily"><i data-lucide="users-round"></i><span><strong>Agent council</strong><small>Bull, bear, risk and execution debate</small></span></button>
        <button class="command" data-action="llm_advice"><i data-lucide="clipboard-check"></i><span><strong>Training advice</strong><small>Structured feature improvements</small></span></button>
        <button class="command" data-action="llm_labels"><i data-lucide="tags"></i><span><strong>Suggest labels</strong><small>Advisory recent-signal labels</small></span></button>
        <button class="command" data-action="llm_train"><i data-lucide="brain-cog"></i><span><strong>Assisted candidate</strong><small>Labels, advice, guarded model fit</small></span></button>
        <button class="command" data-action="llm_cycle" data-cadence="hourly"><i data-lucide="timer-reset"></i><span><strong>Hourly cycle</strong><small>News linkage and focused review</small></span></button>
        <button class="command" data-action="llm_cycle" data-cadence="daily"><i data-lucide="calendar-sync"></i><span><strong>Daily cycle</strong><small>Full FinGPT research workflow</small></span></button>
      </div></article><article class="panel"><div class="panel-head"><h2>Recent AI Activity</h2></div><div id="aiActivity" class="activity-list"></div></article></aside>
  </div>`;
}

function coreMarkup() {
  return `<div class="scene-stage"><div class="scene-heading"><span class="eyebrow">LIVE OPERATIONAL KNOWLEDGE GRAPH</span><h2>GLD System Graph</h2><p>Obsidian-style 3D map of the bot's actual data, intelligence, risk, execution, and learning paths.</p></div>
    <div class="scene-readout scene-left"><span>DECISION PATH</span><strong id="sceneDecision">NO DATA</strong><small id="sceneReason">Awaiting telemetry</small><dl class="scene-probabilities"><div><dt>LONG</dt><dd id="sceneLong">0%</dd></div><div><dt>SHORT</dt><dd id="sceneShort">0%</dd></div><div><dt>ABSTAIN</dt><dd id="sceneAbstain">0%</dd></div></dl></div>
    <div class="scene-readout scene-right graph-node-card"><span>SELECTED NODE</span><strong id="graphNodeLabel">Decision Council</strong><em id="graphNodeStatus">IDLE</em><p id="graphNodeDescription">Combines deterministic agents, playbook quality and model evidence.</p><small id="graphNodeMetric">Awaiting telemetry</small></div>
    <div class="graph-legend"><span><i class="legend-data"></i>DATA</span><span><i class="legend-agent"></i>AGENTS</span><span><i class="legend-model"></i>MODELS</span><span><i class="legend-risk"></i>RISK</span><span><i class="legend-execution"></i>EXECUTION</span><span><i class="legend-memory"></i>MEMORY</span></div>
    <div class="scene-caption"><span class="status-dot"></span><strong>DRAG TO ORBIT · SCROLL TO ZOOM · SELECT A NODE</strong><small>Graph state is live telemetry; LLM nodes remain advisory and have no broker authority.</small></div></div>`;
}

function backtestMarkup() {
  return `<div class="backtest-header"><div><span class="eyebrow">CHRONOLOGICAL SIMULATION</span><h2>Backtest Analytics Lab</h2><p>Run the deterministic strategy against local bars and inspect costs, direction, return, and risk.</p></div><form id="backtestLabForm" class="backtest-controls"><label>Start<input name="start" type="date" value="2025-01-01"></label><label>End<input name="end" type="date" value="2026-01-01"></label><button class="button primary"><i data-lucide="play"></i>Run</button><button class="icon-button" id="refreshBacktestLab" type="button" title="Refresh results"><i data-lucide="refresh-cw"></i></button></form></div>
  <div class="metric-grid compact backtest-metrics"><article class="metric"><span>NET P/L</span><strong id="btNet">--</strong></article><article class="metric"><span>TOTAL RETURN</span><strong id="btReturn">--</strong></article><article class="metric"><span>TRADES</span><strong id="btTrades">--</strong></article><article class="metric"><span>WIN RATE</span><strong id="btWin">--</strong></article><article class="metric"><span>PROFIT FACTOR</span><strong id="btPf">--</strong></article><article class="metric"><span>MAX DRAWDOWN</span><strong id="btDrawdown">--</strong></article></div>
  <div class="backtest-grid"><article class="panel"><div class="panel-head"><h2>Long vs Short Net P/L</h2></div><div class="analytics-chart"><canvas id="btDirectionCanvas"></canvas></div></article><article class="panel"><div class="panel-head"><h2>Outcome Estimate</h2></div><div class="analytics-chart"><canvas id="btOutcomeCanvas"></canvas></div></article><article class="panel"><div class="panel-head"><h2>Average Trade Economics</h2></div><div class="analytics-chart"><canvas id="btEconomicsCanvas"></canvas></div></article><article class="panel backtest-detail"><div class="panel-head"><h2>Validation Detail</h2><span class="badge" id="btState">NO RESULT</span></div><dl class="data-list" id="btDetail"></dl></article><article class="panel terminal-panel span-2"><div class="panel-head"><h2>Backtest Process</h2><span class="badge blue">Structured output</span></div><pre class="terminal" id="backtestLabOutput">No dashboard backtest result is available yet.</pre></article></div>`;
}

function whitePaperMarkup() {
  return `<div class="whitepaper-shell"><aside class="whitepaper-index"><span class="eyebrow">MASHCORP RESEARCH</span><h2>Bot White Paper</h2><p>Versioned technical and operational specification.</p><nav id="whitepaperIndex"></nav></aside><article class="whitepaper-document" id="whitepaperDocument"><div class="empty-state">Loading the white paper...</div></article></div>`;
}
