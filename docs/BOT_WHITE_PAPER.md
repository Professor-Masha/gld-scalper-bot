# Mashcorp GLD Intelligence and Execution System

**Technical White Paper**
**Version 1.0 | August 2026**
**Copyright © Mashcorp. All rights reserved.**

> This document describes a research-oriented algorithmic trading system. It is not investment advice, a promise of profitability, or authorization to deploy unvalidated models with real capital.

## Executive Abstract

The Mashcorp GLD Intelligence and Execution System is a production-style Python platform for researching, simulating, paper trading, and eventually validating systematic GLD strategies. It combines deterministic price-action playbooks, market microstructure, risk and execution controls, classical machine learning, compact causal Transformers, financial-language-model research workflows, and a complete SQLite audit trail.

The system follows one central principle: intelligence may advise, but only the deterministic risk and execution services may authorize broker actions. An LLM can review journals, classify news, retrieve local knowledge, propose labels, and help prepare a model candidate. It cannot bypass stale-data checks, liquidity rules, position limits, reconciliation, or promotion gates.

The current deployment target is Alpaca paper trading. Live deployment remains a separate validation stage requiring clean paper episodes, after-cost profitability, operational reliability, and explicit operator approval.

## Problem Statement

Gold trades respond to several interacting forces: price structure, liquidity, spread, order flow, real and nominal yields, the US dollar, inflation surprises, central-bank communication, geopolitical risk, and session-specific behavior. A chart-only system can mistake a wide-spread move for a breakout. A news-only system can be directionally correct but too late. A model trained without execution costs can report a profit while losing after fills.

The system therefore addresses five connected problems:

- distinguish tradable price movement from noise and poor liquidity;
- preserve causality so future observations never leak into training features;
- estimate outcomes after spread, slippage, and operational costs;
- maintain one consistent episode state from signal through final fill;
- learn from trades and abstentions without allowing research tools to control the broker.

## Objectives And Non-Goals

The objectives are reproducibility, auditability, controlled exploration, cost-aware evaluation, fast inference, and graceful failure. The bot is designed to explain why it traded, why it abstained, how an order progressed, and what the measured outcome was.

The system is not a guaranteed-profit engine. It does not treat an LLM narrative as a price oracle. It does not automatically promote every newly trained candidate. It does not assume that paper fills reproduce live execution. It does not conceal safety failures inside aggregate P/L.

## System Architecture

The runtime is divided into explicit layers:

1. **Data acquisition:** Alpaca bars, quotes, trades, order updates, account state, news, event calendars, FRED macro series, and local knowledge artifacts.
2. **Feature construction:** indicators, price action, market microstructure, gold volatility, order blocks, technical confluence, session context, and freshness diagnostics.
3. **Decision intelligence:** deterministic agents and playbooks, classical model inference, Transformer inference, macro context, and controlled exploration.
4. **Risk authorization:** liquidity, spread, freshness, event, exposure, loss, cooldown, and circuit-breaker checks.
5. **Execution coordination:** one synchronized intent path, idempotent order submission, bracket ownership, fills, replacement, cancellation, reconciliation, and shutdown.
6. **Learning and governance:** outcome labels, journals, missed-opportunity review, offline training, walk-forward testing, promotion, drift monitoring, and rollback.
7. **Operations interface:** localhost-only telemetry, guarded process controls, training workflows, provider management, analytics, backtesting, and documentation.

The versioned architecture diagrams in `docs/architecture/` provide the detailed runtime, decision, order, and training flows.

## Data Model And Causality

Every decision is stored with the information available at decision time. Let (P_t) be the causal reference price and (P_{t+h}) the observed price after horizon (h). The forward return is:

$$
r_{t,h}=\frac{P_{t+h}}{P_t}-1
$$

For a short action, the direction-adjusted return is:

$$
r^{short}_{t,h}=1-\frac{P_{t+h}}{P_t}
$$

The label is cost-aware. With round-trip spread, slippage, fee, and safety-buffer estimate (c_{t,h}), the usable edge is:

$$
e_{t,h}=r^{direction}_{t,h}-c_{t,h}
$$

Labels are generated independently for 1, 3, 5, and 15-minute horizons. One-second snapshots are preferred for fast decisions; bars provide coverage where snapshots are unavailable. Missing-data and session masks prevent absence from being interpreted as a valid zero.

## Market Microstructure

The midpoint and percentage spread are:

$$
M_t=\frac{Bid_t+Ask_t}{2}
$$

$$
SpreadPct_t=\frac{Ask_t-Bid_t}{M_t}
$$

Top-of-book quote imbalance is:

$$
I_t=\frac{BidSize_t-AskSize_t}{BidSize_t+AskSize_t+\epsilon}
$$

The system combines spread regime, quote age, trade age, trade intensity, signed volume, volatility burst, and liquidity score. A connected WebSocket is not sufficient evidence of freshness; the timestamp ages of quotes, trades, and bars are evaluated separately.

## Deterministic Decision Council

The live rule path uses specialized reasoning services:

- **IndicatorAgent** evaluates trend, momentum, VWAP, RSI, moving averages, volatility, Fibonacci context, and technical confluence.
- **PatternAgent** evaluates buildup, range compression, proper breaks, false breaks, tease breaks, pullbacks, retests, support, resistance, fair-value gaps, and order blocks.
- **TrendAgent** evaluates direction and agreement across timeframes.
- **RiskAgent** evaluates whether the proposed action is executable under current data, liquidity, session, event, account, and exposure conditions.

Agent evidence is combined into bullish, bearish, and abstention scores. A score is evidence, not broker authority. The selected playbook must confirm the setup and the risk engine must approve it.

## Strategy Playbooks

Playbooks retain separate confirmation and exit logic because a reversal and a continuation should not share one threshold.

- **Proper breakout:** compressed buildup, structural boundary, confirmed close, volume or flow confirmation, fresh quotes, and acceptable spread.
- **False-break reversal:** boundary sweep, rejection, return into range, opposing flow, and structural invalidation outside the failed break.
- **Pullback continuation:** established trend, orderly retracement into support or resistance, momentum recovery, and favorable liquidity.
- **Compression breakout:** falling range or volatility followed by confirmed expansion.
- **Trend continuation:** multi-timeframe agreement, directional structure, and no exhaustion warning.
- **Spread capture:** tightly controlled microstructure setup requiring stable spread and fresh order flow.
- **News event:** event-aware movement with elevated safety constraints; an event label cannot override stale or dislocated markets.
- **EMA cross:** a translated strategy path whose signals are recorded and evaluated independently.

## Risk And Position Sizing

Position quantity is based on permitted loss and stop distance rather than raw buying power:

$$
Q_t=\left\lfloor\frac{Equity_t\times RiskFraction_t}{|Entry_t-Stop_t|+CostPerShare_t}\right\rfloor
$$

The quantity is then reduced for wide spread, poor liquidity, volatility burst, stale data, weak model confidence, correlated exposure, and experimental status. It may increase only within configured bounds when clean structure, fresh data, good liquidity, and independently validated model evidence agree.

Session loss, drawdown, consecutive-loss, order-rate, aggregate-exposure, open-episode, and circuit-breaker limits remain active in paper mode. Exploration changes sampling, not operational safety.

## Exit Economics

An exit is profitable only after expected round-trip costs and a buffer:

$$
EconomicBreakeven=EntryCost+ExitCost+Slippage+FeeEstimate+SafetyBuffer
$$

The position manager uses setup-specific structural stops, profit targets, trailing-profit rules, maximum profit giveback, invalidation, and maximum holding periods. An emergency risk exit may realize a loss because capital protection takes precedence over the preference to exit profitably. Conflicting entry signals do not directly close an episode; one structural position-management path owns the exit decision.

## Execution Integrity

Each root execution episode owns its parent order, child protection, intended and filled quantity, entry price, exit tranches, costs, P/L, strategy path, playbook, and final close reason. Broker reconciliation compares SQLite episodes, internal state, Alpaca positions, and open orders.

New bracket orders receive a grace period while parent and child identifiers are associated. Residual exposure requires a second broker check. Duplicate callbacks and retries are idempotent. Direction switches cancel existing orders, flatten exposure, reconcile, wait, and only then permit the opposite direction. Session shutdown freezes entries, cancels entries, closes positions, verifies acknowledgements, and confirms zero broker exposure.

## Classical Machine Learning

Classical candidates use supervised classification and regression features derived from causal records. The classifier estimates:

$$
\hat{p}_t=\left[P(LONG|X_t),P(SHORT|X_t),P(NO\_TRADE|X_t)\right]
$$

Cost-aware regressors estimate forward returns and execution cost. Class weighting, balanced sampling, probability calibration, abstention, exact feature profiles, and chronological validation address the dominant no-trade class and prevent accidental evaluation of a different model than the saved candidate.

## Causal Transformer Models

Small encoder-only causal Transformers model sequences rather than isolated rows. Separate scopes cover fast microstructure, minute setups, news events, and exits. Causal masks ensure position (t) cannot attend to observations after (t). Session and missing-data masks distinguish unavailable information.

Transformer outputs include direction probabilities, expected 1/3/5/15-minute returns, estimated cost, and uncertainty. Candidates begin in shadow mode. Inference is asynchronous, and the classical model remains available as a fallback. A Transformer gains authority only after holdout, walk-forward, after-cost, calibration, latency, stability, and paper-performance gates are met.

## FinGPT, Ollama, Kimi, And RAG

Ollama and Kimi are selectable reasoning engines. Ollama provides private local inference suitable for the 8 GB laptop when using a compact model such as `llama3.2:1b`. Kimi is a hosted engine governed by local Tier0 rate and token budgets.

FinGPT is used as a financial workflow and source-code reference: sentiment prompts, forecasting structure, RAG patterns, and financial data preparation. It is not presented as a separately running 7B or 13B local model. Full FinGPT fine-tuning is outside the practical capacity of this laptop and would require a suitable remote GPU environment.

The offline AI workflows can:

- analyze SQLite and exported CSV data;
- review journals and missed opportunities;
- classify news and build macro context;
- score setup quality and suggest features;
- propose advisory labels;
- generate structured training advice;
- run bull, bear, risk, and execution reviews;
- prepare a guarded classical candidate.

LLM output is stored and auditable. It can adjust research confidence or bounded sizing context, but it cannot call broker methods or override execution and risk blocks.

## Training, Validation, And Promotion

Training follows a staged lifecycle:

1. collect and audit historical or paper records;
2. mature multi-horizon outcomes;
3. construct causal training archives or sequences;
4. fit a candidate with exact preprocessing and parameters;
5. evaluate chronological holdout results;
6. evaluate multiple walk-forward folds and regimes;
7. estimate spread, slippage, and live-cost impact;
8. run in shadow mode during paper trading;
9. require sufficient clean paper episodes;
10. compare against the current champion and promotion thresholds;
11. preserve the previous champion for rollback;
12. monitor feature and performance drift.

Repeated offline rounds reuse archived data fingerprints and completed experiment records. The loop stops after configured rounds produce no meaningful walk-forward improvement. Training is scheduled after market hours so the 8 GB laptop does not compete with live data, reconciliation, and order safety.

## Backtesting And Performance Measurement

The backtester uses chronological GLD bars, deterministic feature construction, risk sizing, next-bar entry assumptions, estimated slippage, and cost-aware outcomes. Reports separate root episodes from partial exit tranches and include net P/L, return, trade count, win rate, profit factor, average win and loss, drawdown, Sharpe ratio, holding time, direction results, and consecutive losses.

Backtests have unavoidable limitations. Minute bars do not reconstruct quote queue position or exact intrabar ordering. Paper fills do not guarantee live fills. A positive historical result therefore justifies additional validation, not live deployment.

## Operations And Security

The dashboard binds to localhost. State-changing requests require a random session token. Secrets remain in the ignored local `.env` file and are never returned to the browser. Provider tests reveal status and model names, not keys. Dashboard actions map to an explicit CLI allowlist.

The interface can supervise paper trading, data collection, labeling, ML training, Transformer training, research jobs, analytics, and backtests. It cannot directly submit an order. The execution engine remains the sole broker-authority boundary.

### Volatility And Tail-Risk Research Workstation

The Volatility Lab reconstructs the useful workflow shown in the supplied self-improving-agent reference: controls, a moving temporal network, a statistical verdict, and synchronized diagnostics. It uses only chronologically ordered local GLD one-minute closes. Log returns are calculated as `ln(P_t / P_(t-1))`; rolling sample deviation estimates current volatility. Empirical quartile-style thresholds divide the rolling series into low, normal, high, and extreme regimes. The transition matrix estimates the conditional probability of the next regime, while current run length and same-state transition probability measure persistence.

Historical Value at Risk selects the configured loss quantile. Conditional Value at Risk averages observations in that tail. The displayed size multiplier is the risk budget divided by CVaR, reduced by a regime factor and clamped to a conservative range. This is an explanatory research value, not a live risk instruction. It cannot modify settings, place an order, or override freshness, liquidity, reconciliation, session, drawdown, or circuit-breaker controls. A production sizing rule based on this research requires separate chronological backtesting and clean paper validation.

## Limitations And Risks

- IEX data is not a complete view of the US consolidated market.
- Historical quotes and trades may contain gaps or provider-specific conditions.
- News timestamps, summaries, and sentiment labels can be incomplete or delayed.
- LLM output can be plausible but wrong and must remain advisory.
- Backtest cost assumptions may underestimate stressed or news-driven execution.
- Compact laptop models trade capacity for speed and memory safety.
- Strategy behavior can degrade when the market regime differs from training data.
- Paper profitability does not prove live profitability.

## Governance And Deployment Criteria

Live deployment should be considered only after execution consistency checks pass, clean episodes are available across playbooks and regimes, after-cost paper profit factor exceeds the approved floor with uncertainty bounds, drawdown remains within policy, model calibration is acceptable, no unresolved drift or reconciliation alert exists, rollback is tested, and the operator explicitly approves the promoted artifact.

Every release should update code, tests, README documentation, this white paper when architecture changes, and the private Git repository history. Secrets and source training datasets must remain outside version control.

## Roadmap

Near-term priorities are clean execution evidence, reliable provider diagnostics, richer news and event linkage, independent fast/minute strategy evaluation, trustworthy exit outcomes, and enough paper episodes for statistically meaningful model comparison. Future work may include cloud-based FinGPT fine-tuning, broader market-data feeds, and live deployment only after paper validation.

## Conclusion

The Mashcorp bot is best understood as a governed research and execution platform, not a single indicator. Its advantage is the agreement of data quality, deterministic playbooks, measured model evidence, risk authorization, broker reconciliation, and auditable learning. The system becomes more useful when those components describe the same trade accurately from observation to final outcome.

## References

- Alpaca Trading API documentation: [https://docs.alpaca.markets/](https://docs.alpaca.markets/)
- Vaswani et al., *Attention Is All You Need*: [https://arxiv.org/abs/1706.03762](https://arxiv.org/abs/1706.03762)
- AI4Finance Foundation, FinGPT: [https://github.com/AI4Finance-Foundation/FinGPT](https://github.com/AI4Finance-Foundation/FinGPT)
- AI4Finance Foundation, FinRL: [https://github.com/AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL)
- Tauric Research, TradingAgents: [https://github.com/TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
- Ollama documentation: [https://docs.ollama.com/](https://docs.ollama.com/)
- Federal Reserve Economic Data API: [https://fred.stlouisfed.org/docs/api/fred/](https://fred.stlouisfed.org/docs/api/fred/)
