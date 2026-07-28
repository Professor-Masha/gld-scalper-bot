# GLD Scalper Bot

Copyright (c) 2026 @Mashcorp. All rights reserved. Mashcorp and @Mashcorp are claimed trademarks/marks of their owner. This codebase is proprietary software. Do not copy, redistribute, resell, sublicense, or use the name or marks without written permission from @Mashcorp.

## What This Bot Is

GLD Scalper Bot is a paper-trading algorithm for the `GLD` ETF using Alpaca's paper trading API. It watches live market data, stores that data in SQLite, evaluates its full strategy once per minute, and also evaluates quote/trade microstructure on a sub-second event path. It submits protected paper bracket orders and turns completed outcomes into training evidence.

Normal paper mode is intentionally conservative. The optional controlled paper-learning profile samples a limited number of small, near-valid probes. Both modes still refuse stale or disconnected data, failed broker reconciliation, mixed-direction GLD exposure, and unexpected open orders.

This bot is paper trading only. It is not financial advice, and paper trading results do not guarantee live trading results. Alpaca paper trading is a simulation, not a perfect copy of live market execution.

## What The Bot Does Every Minute

1. Syncs recent Alpaca GLD paper orders back into SQLite.
2. Records fills and completed bracket trade outcomes when Alpaca reports them.
3. Reviews every newly closed trade, records what helped or hurt it, and creates a supervised training label.
4. Exports hourly CSV files when the export interval is due.
5. Updates slow macro/sentiment context when the hourly macro interval is due.
6. Reviews matured no-trade signals for missed opportunities.
7. Starts safe scheduled retraining only in the configured after-hours window and only when no execution episode is open.
8. Reads the latest SQLite bars, quote, and trade.
9. Builds indicators, price action, microstructure, gold-volatility, macro, and market-context features.
10. Runs the loaded ML model and stores its probabilities, confidence, model version, role, and abstention reason for every completed minute.
11. Runs the reasoning agents and scores `LONG`, `SHORT`, and `NO_TRADE`, including only a small bounded ML adjustment in paper-learning mode.
12. Converts the decision into a target exposure suggestion.
13. Checks broker account state, open positions, open orders, stale data, session, spread, and shortability.
14. Submits one Alpaca paper bracket limit order only if every safety layer passes.

## Full Operating Model

This section explains the bot as one complete system. Read this before changing settings or running paper trading for the first time.

At a high level, the bot has three separate jobs:

- **Live trader**: collect market data, score setups, manage risk, and place paper trades.
- **Research engine**: record everything, label outcomes, export CSVs, and train candidate models.
- **LLM analyst**: use local Ollama to review data and suggest improvements without controlling live trades.

These jobs are intentionally separated. The live trading path must stay predictable, fast, and rule-governed. The LLM path is slower and more flexible, so it is used for review, training advice, and research rather than order execution.

### 1. Configuration And Safety Startup

When the bot starts, it first loads settings from `.env`.

Important examples:

```dotenv
ALPACA_PAPER=true
ALPACA_PAPER_TRADE=true
ALPACA_ENDPOINT=https://paper-api.alpaca.markets/v2
BOT_SYMBOL=GLD
BOT_DATA_MODE=paper
DATABASE_URL=sqlite:///data/paper/gld_scalper.db
LLM_PROVIDER=ollama
LLM_MODEL=llama3.2:1b
ENABLE_LLM_LIVE_TRADING=false
```

The settings layer performs basic safety checks before trading logic can run:

- the bot must be in paper mode
- the Alpaca endpoint must be the paper endpoint
- the trade symbol must be `GLD`
- non-SQLite databases are rejected
- project-local database and export paths must stay inside the matching paper/live folder
- short trading must still pass Alpaca asset checks
- the LLM must not be allowed to control live trading

If these assumptions are wrong, the bot should fail early instead of silently trading in an unsafe mode.

### 2. Alpaca Connections

The bot uses Alpaca for two separate things:

- **Trading API**: account state, open positions, open orders, submitted paper orders, fills, and order status.
- **Market data API**: historical bars and live bars/quotes/trades.

The bot trades only through Alpaca paper trading. It uses the Trading API to submit bracket orders and the data stream to keep market context fresh.

The main traded symbol is:

```text
GLD
```

The supporting symbols are:

```text
GDX
GDXJ
IAU
QQQ
SHY
SLV
SPY
IEF
TLT
UUP
VIXY
```

Those supporting symbols help the bot understand the broader gold environment:

- `UUP`: US dollar proxy. Gold often reacts to dollar strength/weakness.
- `TLT`, `IEF`, and `SHY`: Treasury/rates proxies across the curve.
- `SPY`, `QQQ`, and `VIXY`: equity/risk appetite and volatility proxies.
- `SLV`, `IAU`, `GDX`, and `GDXJ`: related precious-metal, gold ETF, and miner context.

The bot does not trade all of these symbols. They are context inputs for `GLD`.

### 3. SQLite Is The Bot's Memory

The SQLite database is the central memory of the bot:

```text
data/paper/gld_scalper.db
```

Paper and future live trading data are intentionally separated:

```text
data/paper/gld_scalper.db
data/live/gld_scalper.db
exports/paper/hourly/
exports/paper/latest/
exports/live/hourly/
exports/live/latest/
```

The current bot is still paper-trading only, so `BOT_DATA_MODE=paper` is the active mode. The live folders are a prepared structure for a later live-trading engineering pass. Keeping these folders separate prevents paper fills, paper labels, no-trade learning, and model-training evidence from being mixed with future real-money records.

Nearly every important event is written there:

- market bars
- live quotes
- live trades
- strategy signals
- no-trade explanations
- paper orders
- fills
- completed trade outcomes
- trading journal entries
- missed-opportunity labels
- macro context
- LLM reviews
- LLM training advice
- candidate/champion model records
- system logs

This matters because the bot is not just trying to trade today. It is trying to build a reviewable dataset that can improve the rules and train future candidate models.

Think of SQLite as the bot's notebook. If something happens and it is not in SQLite, the bot cannot reliably learn from it later.

The live process has several threads that can write at nearly the same time: stream persistence, fast-decision persistence, structured logging, the minute loop, exports, and retraining. The database layer therefore uses SQLite WAL mode, a configurable busy timeout, and one process-wide reentrant write lock. `DATABASE_BUSY_TIMEOUT_MS=30000` gives an external SQLite writer up to 30 seconds to finish. A logging write failure is contained by the logging handler, and `Database.log_event()` will not terminate the trading loop if diagnostic logging itself cannot write. Keep only one `run-paper` process active; the lock coordinates this bot's threads, not two unrelated bot processes.

### 4. Live Data Flow

The live stream collector listens for:

- `GLD` trades
- `GLD` quotes
- minute bars for `GLD` and context symbols

The bot tracks diagnostics such as:

- whether the websocket is connected
- quote count
- trade count
- last live message time
- last live bar time
- market data age
- whether data is stale

This is why logs can say things like:

```text
websocket disconnected
market data stale
live stream stale
```

Those messages are protective. A scalping bot should not trade when the data feed is old, disconnected, or internally inconsistent.

### 5. Fast Event-Driven Scalping Layer

The bot now has two decision speeds:

```text
Fast layer:      quote/trade event driven, target interval about 250 ms
Slow layer:      one-minute bar/context loop
```

The fast layer is designed for:

- tiny spread captures
- very fast false breaks
- news-spike volatility bursts
- bid/ask microstructure scalps
- clean live breakouts
- spread expansion detection
- liquidity collapse detection
- sudden volatility burst detection

The Alpaca websocket still saves live quotes and trades to SQLite, but it also hands compact quote/trade events to a separate fast worker thread. That worker keeps an in-memory rolling window of quotes and trades, so it does not need to query SQLite before deciding.

This is the important speed difference:

```text
Old behavior:
live data -> SQLite -> wait until next minute -> decision

Fast behavior:
live quote/trade -> in-memory fast engine -> decision in milliseconds
```

The fast engine builds only lightweight microstructure features:

- bid
- ask
- midpoint
- spread percentage
- quote imbalance
- trade intensity
- aggressive buy/sell pressure
- signed volume
- realized range over the last few seconds
- liquidity score
- spread expansion ratio
- volatility burst flag

It classifies fast triggers such as:

```text
clean_breakout
false_break
spread_capture
news_spike
fast_block
microstructure
```

Fast decisions are saved to:

```text
fast_scalp_decisions
exports/paper/hourly/.../microstructure/fast_scalp_decisions/
```

The fast layer can submit paper orders when enabled, but it still uses the normal risk engine, broker checks, ML predictor, cooldown, open-position guard, and bracket-order execution. It is faster, but it is not allowed to bypass safety.

Useful fast-layer settings:

```dotenv
ENABLE_FAST_SCALP=true
ENABLE_FAST_SCALP_ORDER_SUBMISSION=true
FAST_SCALP_INTERVAL_MS=250
FAST_SCALP_ORDER_COOLDOWN_SECONDS=30
FAST_SCALP_TIGHT_SPREAD_PCT=0.00035
FAST_SCALP_BREAKOUT_MIN_MOVE_PCT=0.00025
FAST_SCALP_IMBALANCE_THRESHOLD=0.30
FAST_SCALP_MIN_TRADE_INTENSITY=0.40
FAST_SCALP_MIN_CONFIDENCE=0.62
FAST_SCALP_VOLATILITY_BURST_PCT=0.0010
FAST_SCALP_MAX_QUOTE_AGE_SECONDS=2
FAST_SCALP_MAX_TRADE_AGE_SECONDS=3
FAST_SCALP_MIN_SPREAD_STABILITY=0.55
MINUTE_ENTRY_MAX_QUOTE_AGE_SECONDS=15
MINUTE_ENTRY_MAX_TRADE_AGE_SECONDS=30
MINUTE_ENTRY_MIN_TRADE_INTENSITY=0.05
MINUTE_ENTRY_MIN_SPREAD_STABILITY=0.35
```

For rookie developers: this does not make the laptop an HFT server. Alpaca paper order submission and internet latency are still outside the bot's control. What this upgrade changes is the bot's internal reaction time: it can recognize a fast setup immediately from live quote/trade events instead of waiting for the next minute bar.

Every entry is tagged with a New York time-of-day profile: `open`, `morning`, `mid_session`, `afternoon`, or `close`. Mid-session entries require a proper break unless they are a confirmed false-break reversal. Close-profile entries require a high-scoring breakout or trend continuation, and Phase 1's global freeze still blocks every new entry during the final 15 minutes. Reports compare the profiles through their New York entry hour.

### 6. Feature Construction

Once per loop, the bot reads the latest market state and builds a feature snapshot.

The feature snapshot can include:

- raw price and volume
- VWAP position
- moving average behavior
- RSI/MACD-style momentum
- ATR and volatility
- spread percentage
- quote imbalance
- trade intensity
- liquidity score
- support and resistance levels
- range compression
- price-action pattern classification
- gold-specific volatility regime
- macro context
- previous model prediction when a champion model exists

The bot stores the feature snapshot with signals and journal entries so later analysis can explain what the bot saw at the time.

### 7. Price-Action Pattern Engine

The price-action layer tries to describe the setup in trader language instead of only indicator numbers.

It looks for patterns such as:

- **buildup**: price compresses near an important level before a possible move.
- **proper break**: price breaks a level with cleaner confirmation.
- **false break**: price breaks a level but quickly fails.
- **tease break**: price touches or briefly crosses a level without enough confirmation.
- **pullback**: price returns toward a level after a move.
- **support/resistance**: nearby levels that may reject or attract price.
- **range compression**: price narrows into a tight range before volatility expands.

The pattern engine does not place trades by itself. It feeds pattern quality into the final decision and risk sizing.

Playbooks are evaluated separately; they do not share one generic confirmation threshold:

- `proper_breakout` requires a proper break, pattern quality, volume, and buildup or compression evidence.
- `buildup_break` requires buildup, compression, a proper break, and volume confirmation.
- `compression_breakout` requires compression, a proper break, volume expansion, and pattern quality.
- `false_break_reversal` requires a false-break classification, wick rejection, return inside the prior range, and no unconfirmed volatility burst.
- `pullback_continuation` requires the pullback pattern, one-minute and five-minute trend agreement, and an EMA/VWAP reclaim.
- `trend_continuation` requires a full trend stack, five-minute alignment, trend strength, and usable volume.
- `news_event` is post-release only and requires an identified release state, volatility burst, proper break, and directional microstructure pressure.
- `spread_capture` exists only on the fast path and requires a fresh, stable, tight spread plus directional tape.

The selected playbook stores its confirmation names, confirmation count, required count, score, direction, and block reason. This lets the report answer not only which playbook traded, but which exact confirmation was missing from a rejected setup.

#### Multi-Timeframe Order-Block Intelligence

The deterministic engine in `src/gld_scalper/order_blocks.py` treats an order block as a **price zone inferred from completed candles**, not proof that one institution placed a hidden order. Alpaca's normal GLD quote/trade feeds do not identify institutions and are not a full depth-of-book feed.

The engine examines completed 1, 5, 15, 30, 45, and 60-minute candles. A zone is accepted only after an opposing origin candle is followed by an ATR-scaled displacement, a break of recent structure, sufficient volume expansion, and at least two completed post-origin candles.

For every accepted zone it records:

- bullish or bearish direction
- zone low and high
- origin and confirmation times
- displacement percentage and ATR multiple
- volume ratio and break-of-structure confirmation
- fair-value-gap observation
- retest count, mitigation, invalidation, age, and strength

Using completed candles reduces repainting. One-minute zones can be confirmed within several completed minutes. A 60-minute zone necessarily takes longer because its confirming hourly candles must close. After confirmation, the fast engine compares each eligible live midpoint with the saved zone, so a retest can be recognized on the sub-minute path.

Order blocks remain bounded context:

- a strong aligned retest can add up to 6 rule points
- broad multi-timeframe alignment can add 3 rule points
- a clean aligned retest can increase proposed size modestly
- an opposing zone or nearby bullish/bearish conflict reduces size
- a zone cannot bypass spread, liquidity, stale-data, ML, position, broker, or session checks
- a zone cannot create a trade without a valid price-action or microstructure setup

Zones are stored in `order_block_zones` and exported under the `patterns` category.

#### GLD Options Intelligence

The bot collects **GLD listed-option chain snapshots** through Alpaca. It does not scrape the Investing.com XAU/USD options page. XAU/USD FX options and GLD equity options are different instruments, and an HTML page is not a stable broker API.

The background collector requests GLD strikes near the current price and expirations inside the configured window. It runs independently, so a slow options request cannot delay the quote/trade scalping engine.

Per contract, it stores the call/put type, strike, expiration, GLD reference price, bid/ask and sizes, midpoint, spread, latest trade, implied volatility, Greeks, quote age, feed, and source. The aggregate layer estimates:

- call-to-put recent activity ratio
- call and put aggressive-flow proxies
- at-the-money implied volatility and put/call IV skew
- expiration-scaled expected move
- option-chain freshness and liquidity
- options-implied event risk
- bullish, bearish, or neutral GLD context
- confidence and a bounded score adjustment

This is an **activity proxy**, not proof from full historical volume or open interest. A trade near the ask is treated as aggressive buying and one near the bid as aggressive selling, but that inference is imperfect. Alpaca's free `indicative` feed is delayed and its quotes are modified. Select `opra` only when the Alpaca account has the required entitlement.

Options intelligence is deliberately weaker than price action:

- it contributes at most `OPTIONS_MAX_SCORE_ADJUSTMENT`, default 3 points
- it can make only a small size adjustment when fresh, liquid, confident, and aligned
- elevated options event risk can reduce size and increase the no-trade score
- stale or missing options data becomes neutral
- it never directly submits an option or GLD order
- the bot still trades GLD shares only

Contracts are stored in `option_snapshots`; aggregate records are stored in `options_intelligence`. Both export under the `options` category.

### 8. Microstructure Layer

The microstructure layer checks whether the market is clean enough for a short-term trade.

It can evaluate:

- spread regime
- quote imbalance
- trade intensity
- liquidity score
- volatility bursts
- stale quote/trade conditions

For scalping, this matters a lot. A setup can look good on indicators but still be poor if the spread is wide, quotes are thin, or live trade flow is weak.

### 9. Gold-Specific Volatility Layer

Gold does not move the same way all day. The bot includes a gold-specific volatility module that can consider:

- time-of-day behavior
- volatility regime
- intraday seasonality
- cycle/phase style features

The goal is not to predict the future perfectly. The goal is to avoid treating every minute of the session as equal.

For example, a move during a quiet liquidity pocket should be treated differently from a move during a cleaner, more active period.

### 10. Internal Reasoning Agents

The bot uses deterministic internal "agents." These are not LLM agents. They are structured code modules that review different parts of the setup.

Main internal agents:

- `IndicatorAgent`: checks indicator agreement, momentum, VWAP, and trend evidence.
- `PatternAgent`: checks buildup, break quality, compression, pullback, and pattern score.
- `TrendAgent`: checks whether short-term and context trends agree.
- `OrderBlockAgent`: reviews confirmed zones, multi-timeframe direction, retests, and conflicts.
- `OptionsAgent`: reviews fresh, liquid, confidence-weighted GLD options context.
- `RiskAgent`: checks liquidity, spread, volatility, stale data, and risk blocks.

Their outputs are combined into the final signal. This gives the bot a more explainable decision process than a single black-box score.

### 11. Strategy Decision

The strategy engine scores three possible outcomes:

```text
LONG
SHORT
NO_TRADE
```

It also calculates:

- bullish score
- bearish score
- no-trade score
- confidence
- regime
- explanation text

The bot can skip trades for many healthy reasons:

- bullish and bearish evidence are too close
- price is chopping around VWAP
- one-minute and five-minute context disagree
- liquidity is poor
- spread is too wide
- ATR is too low
- market data is stale
- live stream is disconnected
- no champion model exists and rule score is not extreme
- regime is unfavorable

`NO_TRADE` is not failure. For this bot, no-trade decisions are part of risk control and part of the learning dataset.

### 12. Target Exposure

Before execution, the bot converts the signal into a target exposure idea.

Instead of thinking only:

```text
buy or sell
```

the strategy expresses something closer to:

```text
desired GLD exposure = 0%, small long, or small short
```

Then the risk engine can adjust that exposure based on:

- signal confidence
- pattern quality
- liquidity score
- spread
- volatility
- macro alignment
- current account and position state

This makes live trading, backtesting, and future strategy improvements easier to keep consistent.

### 13. Risk Engine

The risk engine is a hard safety layer. It can reduce size or block a trade even when the strategy likes the setup.

It checks:

- paper account status
- buying power
- open `GLD` position
- open `GLD` orders
- session allowed
- stale market data
- stale live stream
- websocket disconnected
- spread too wide
- poor liquidity
- high volatility
- daily loss limits
- shortability for short trades
- macro event risk when available

The risk engine decides whether the bot may create an order plan.

If risk blocks the trade, the bot logs a no-trade reason and does not submit an order.

### 14. Paper Order Execution

If strategy, risk, model, and broker checks all pass, the execution engine submits a paper bracket order to Alpaca.

A bracket order includes:

- entry order
- take-profit exit
- stop-loss exit

The bot uses bracket orders because they make the intended exit plan explicit at order creation time.

The execution engine records:

- side
- quantity
- entry price
- stop price
- take-profit price
- client order id
- Alpaca order id
- broker response

When `ENABLE_PARTIAL_PROFIT_TRANCHES=true` and the calculated quantity is at least two shares, the execution engine divides the entry into two independently protected bracket episodes:

- `-TAKE`: the first portion uses the normal take-profit price.
- `-RUN`: the remaining portion uses a farther target controlled by `PARTIAL_PROFIT_RUNNER_TARGET_MULTIPLIER`.

Each portion has its own broker-resident catastrophic stop. The bot does not submit an unprotected entry and does not cancel a stop before an exit is accepted. This structure lets one portion realize profit while the runner remains protected.

#### Centralized Execution Safety

All production broker writes now pass through one `OrderIntentCoordinator`. The minute loop and fast loop do not call Alpaca independently, and the dynamic position manager does not replace exit legs independently. Entry submissions, stop replacements, protected exits, cancellations, direction changes, residual cleanup, and shutdown liquidation enter the same synchronized queue and execute one at a time.

Every queued operation has an idempotency key. Repeating the same entry callback reuses the first result instead of submitting a second order. A timed-out entry is not blindly submitted again. Before a new entry reaches Alpaca, the coordinator also checks Alpaca by `client_order_id`; only a confirmed not-found response permits creation. This is important because a local timeout does not prove that Alpaca rejected the original request.

`run-paper` starts with entries frozen and performs a safety reconciliation before either decision runtime starts. It compares:

- the GLD position and direction reported by Alpaca
- all open GLD broker orders, including nested bracket legs
- active episodes reconstructed from `orders`
- atomic episodes in `execution_episodes`
- the in-memory episodes managed by the position runtime after that runtime starts
- whether every broker position has an active broker-resident protective stop

The supervisor repeats this check every `EXECUTION_RECONCILE_INTERVAL_SECONDS`, which defaults to three seconds. A broker position with no database episode, no protective stop, or unknown orders is treated as residual exposure. With the default `EXECUTION_FLATTEN_RESIDUAL_POSITIONS=true`, the bot cancels GLD orders, waits for cancellation acknowledgements, closes GLD, and verifies that both the broker position and open-order list are empty before entries are released. It never tries to reconstruct an imaginary stop from incomplete local information.

An opposite-direction signal uses a controlled direction switch. The bot freezes entries, cancels existing GLD orders, closes the current net position, polls Alpaca until flat, reconciles SQLite, waits the configured cooldown, and only then permits the opposite entry. GLD remains one net Alpaca position; this procedure does not pretend that independent long and short holdings can coexist.

The regular-session shutdown has two boundaries:

1. At 15 minutes before close, `session_close_window` freezes every new minute and fast entry.
2. At 10 minutes before close, the supervisor cancels entries and protective orders, closes any remaining GLD position, waits for broker confirmation, reconciles final fills, and requires zero GLD position plus zero open GLD orders.

Pressing `Ctrl+C` follows the same verified flatten procedure when `EXECUTION_FLATTEN_ON_SHUTDOWN=true`. Do not terminate the Python process from Task Manager unless the normal shutdown is genuinely stuck, because a forced process kill cannot wait for broker confirmation.

The circuit breaker counts consecutive broker-write failures, stale/disconnected stream checks during an open market, reconciliation mismatches, and unrecoverable order-state errors. Reaching a configured threshold latches the breaker and freezes new entries for the rest of that process. A restart is required after the underlying cause has been inspected; successful unrelated checks do not silently clear a latched circuit.

The safety audit is durable:

- `execution_episodes` owns episode direction, quantity, fill totals, remaining quantity, average entry/exit, realized P/L, lifecycle status, and version.
- `execution_episode_orders` links entry, protective, replaced, and exit orders to the episode.
- `order_intents` records every queued, running, completed, failed, or timed-out broker write.
- `execution_safety_events` records reconciliation snapshots, flatten confirmations, and circuit trips.

These tables are included in hourly CSV exports under the `execution_safety` folder.

#### Event-Driven Position Management

`run-paper` starts a separate dynamic position-management worker whenever live streaming and `ENABLE_DYNAMIC_POSITION_MANAGEMENT` are enabled. It receives every live GLD quote and trade directly from the websocket route. Management evaluations are throttled to `POSITION_MANAGER_INTERVAL_MS`, which defaults to 250 milliseconds; they do not wait for the next one-minute candle.

The exit hierarchy is deliberately different from the old tight fixed-stop behavior:

1. Every entry is created with a broker-resident catastrophic stop. Alpaca can execute this protection even if the local process or internet connection later fails.
2. An ordinary temporary loss receives recovery room. Reaching `MAX_HOLDING_MINUTES` does not close a losing trade by itself.
3. A losing episode can request an early protected exit only when at least `POSITION_INVALIDATION_REQUIRED_VOTES` independent context sources strongly invalidate the setup. The possible votes are the deterministic signal, the ML model, the order-block engine, and macro context.
4. A profitable episode can move its stop to breakeven after `POSITION_BREAKEVEN_TRIGGER_PCT`.
5. A stronger favorable move activates a one-direction trailing stop. The stop may tighten but can never move backward and increase risk.
6. A trade beyond the normal holding period is closed only when its profit exceeds the configured buffer and estimated spread allowance.
7. Any remaining episode is flattened shortly before the regular session close so a paper scalp does not silently become an overnight position.

The bot cannot honestly guarantee that every exit will be profitable. A catastrophic stop, a strongly invalidated setup, or session-close protection may realize a loss. This is intentional: refusing every losing exit can turn a small scalp loss into an uncontrolled position. Stop orders can also fill away from their stop price during a gap or fast market.

The manager changes an existing bracket leg through Alpaca order replacement. Every request, failure, old stop, new stop, current mark, P/L, maximum favorable excursion, and maximum adverse excursion is saved in `position_management_events`. Restart state is saved in `position_management_state` and reconciled against the broker before further management.

#### Sub-Minute Price Record

The websocket still stores raw quotes and trades. In addition, the position worker produces one compact `price_snapshots` row per second with bid, ask, midpoint, last trade, spread, spread percentage, quote age, and trade age. This gives analysis and future ML training a practical sub-minute price series without duplicating every tick into another large table.

### 15. Reconciliation

After orders are submitted, the bot cannot simply assume they filled or closed. It reconciles with Alpaca.

The order reconciler:

- fetches recent paper orders
- updates local order status
- records fills
- detects closed bracket trades
- calculates completed trade outcomes from actual paper fills
- reconstructs maximum favorable excursion, maximum adverse excursion, and the best observed exit time
- writes close and post-trade review journal entries

This keeps SQLite aligned with the broker instead of trusting only local assumptions.

There are two reconciliation speeds. The safety supervisor performs a lightweight broker/database/runtime comparison every few seconds and controls the entry gate. The detailed order reconciler runs through recent nested orders, persists order and fill changes, builds completed outcomes, and links those updates back to atomic execution episodes. Session shutdown explicitly runs detailed reconciliation after Alpaca confirms the account is flat.

Alpaca bracket exit legs use closing intents such as `sell_to_close` and `buy_to_close`. They are not new positions. The reconciler assigns `position_side` only to true opening parents, saves nested legs with `parent_order_id`, and explicitly excludes closing orders from active-episode queries. Database initialization also repairs older rows that were incorrectly classified. This prevents a short exit buy from creating a false LONG episode, a long exit sell from creating a false SHORT episode, or the submission gate from reporting a false mixed-direction position.

#### Accurate Performance Accounting

Performance is measured from broker-confirmed state, not from submitted orders. The bot records an account snapshot at startup, at most once every minute, at the end-of-session flatten, and at shutdown. Each snapshot includes equity, cash, buying power, portfolio value, realized P/L from completed outcomes, broker unrealized P/L, the session equity baseline and peak, current drawdown, drawdown percentage, open positions, and open orders.

Every fill is joined to the most recent quote at or before the broker fill time. The `fills` row preserves:

- bid, ask, midpoint, spread, and spread percentage
- expected executable price: ask for a buy or bid for a sell
- submitted limit or stop price
- actual broker fill price
- adverse slippage beyond the executable touch
- estimated half-spread cost, fee estimate, and total estimated live-trading cost
- root episode ID and `fast` or `minute` strategy path

Spread and slippage are deliberately separated. Slippage is not measured from the midpoint because doing so and then adding spread cost would count the same half-spread twice.

Completed outcomes retain gross P/L and net P/L after spread, adverse slippage, fees, and the configured live-cost estimate. The post-trade review adds maximum favorable excursion, maximum adverse excursion, opportunity cost, and profit given back before exit.

A protected entry can have two exit tranches, but it is one trading idea. Reports therefore show both:

- root trading episodes, used for episode count, win rate, profit factor, and strategy comparisons
- partial exit tranches, used to audit individual fills and exit behavior

The daily report breaks root episodes down by direction, strategy path, playbook, regime, New York entry hour, exit reason, ML prediction, and confidence band. This makes `fast` and `minute` performance independently visible.

At end-of-session and shutdown, `performance_consistency_audits` checks for unmatched fills, orphan child orders, open execution episodes, remaining Alpaca GLD exposure, remaining open orders, and database/broker quantity disagreement. A failed audit means the session is not clean even if a P/L number was produced.

### 16. Trading Journal

The trading journal is the human-readable explanation layer.

It records important trade-related events such as:

- `TRADE_DECISION`
- `ORDER_SUBMITTED`
- `ORDER_SUBMIT_FAILED`
- `ORDER_FILLED`
- `TRADE_CLOSED`
- `TRADE_REVIEW`

The journal is designed so a human can later ask:

```text
Why did the bot do this?
What pattern did it see?
What was the liquidity?
What did risk allow?
What did the model say?
What happened after entry?
```

The LLM can also use this journal later during offline review.

#### Trade-By-Trade Learning

Every reconciled closed paper trade becomes a durable learning record. The review engine joins the result to the original signal and order decision, then preserves:

- the complete entry feature snapshot and agent votes
- actual entry, exit, quantity, notional, holding time, and estimated net return
- maximum favorable excursion and maximum adverse excursion
- setup quality, pattern, liquidity, spread, volatility, macro, and order-block context
- factors that helped the trade and factors that hurt it
- a deterministic mistake category for a bad trade
- a counterfactual describing what condition should have caused a skip or a better exit
- whether the trade was a normal setup or a deliberately small paper exploration

The result is written to `trade_reviews`, a `TRADE_REVIEW` journal row, and an `outcome_labels` training row linked to the original signal. A profitable long becomes `long_good`, a profitable short becomes `short_good`, and a losing or flat trade becomes `no_trade`. This is delayed supervised learning: the bot waits for the broker-confirmed outcome before creating the lesson, so it never teaches itself from an assumed fill or an unfinished trade.

The review is deterministic and runs inside reconciliation. Ollama may explain these records later, but an LLM cannot rewrite the realized result, place an order, or promote a model.

#### Bounded Paper Exploration

The bot may take a very small trade that the normal strategy skipped when the setup is close to qualifying. This creates real paper execution evidence for uncertain but promising conditions. It is not unrestricted risk taking. Exploration is paper-only and requires fresh connected data, regular market hours, a strong directional score and score gap, tight spread, adequate liquidity, no high-impact event risk, no volatility emergency, and no conflicting playbook or strong order block.

Default limits are deliberately small:

```dotenv
ENABLE_PAPER_EXPLORATION=true
PAPER_EXPLORATION_MAX_TRADES_PER_DAY=2
PAPER_EXPLORATION_COOLDOWN_MINUTES=60
PAPER_EXPLORATION_MAX_NOTIONAL=1000
PAPER_EXPLORATION_MIN_SCORE=70
PAPER_EXPLORATION_MIN_SCORE_GAP=20
PAPER_EXPLORATION_MAX_NO_TRADE_SCORE=65
PAPER_EXPLORATION_MIN_LIQUIDITY_SCORE=0.70
PAPER_EXPLORATION_MAX_SPREAD_PCT=0.0008
```

An exploration trade still passes through the normal risk engine, position checks, open-order checks, shortability check, bracket-order execution, reconciliation, and model-promotion rules. It cannot run in live mode. Its review is explicitly tagged so training and human analysis can compare exploration evidence with normal strategy trades.

#### Controlled Paper Learning Mode

`PAPER_LEARNING_MODE=true` enables paper-only data collection, bounded ML advice, and a distinct exploration lane. It does not force the bot to trade and it does not turn every `NO_TRADE` into an order. The normal strategy keeps its live-representative standards; only candidates explicitly tagged `paper_exploration=true` are evaluated by the lower exploration thresholds.

Normal entries require a confirmed playbook. A skipped but near-valid setup can become a small exploration order only when deterministic, informative sampling selects it. The base sample rate is 25%. The probability may increase, to a maximum of 75%, for a decision-boundary score, close model probabilities, model/rule disagreement, indicator disagreement, one missing playbook confirmation, boundary liquidity, or an underrepresented direction, playbook, or regime. Sampling is reproducible from the decision timestamp, symbol, strategy path, playbook, regime, and scores.

Every candidate records `exploration_selection_probability`, `exploration_selection_bucket`, `exploration_priority_reasons`, and an inverse `exploration_propensity_weight`. This allows later training and analysis to account for the fact that exploration executes a selected subset instead of treating the subset as an unbiased sample.

The following conditions remain hard blocks in learning mode:

- stale or disconnected quotes and trades
- wide or unstable spreads
- poor liquidity or uncontrolled high volatility
- low-volatility and sideways fast setups unless the paper exploration lane has a recognized, directionally valid playbook
- active high-risk event windows unless the dedicated post-release news playbook confirms
- missing or conflicting playbook direction
- order-block conflict
- failed broker reconciliation, shortability, exposure, or execution-safety checks
- post-loss cooldown or repeated losses in the same strategy/playbook/regime

The fast path no longer creates a direction from neutral tape and no longer alternates LONG and SHORT by clock time. It needs fresh quote and trade timestamps, minimum trade intensity, stable spread, adequate liquidity, confidence, and one of its specific playbooks: false-break reversal, proper breakout, spread capture, or post-release news event.

Minute and fast strategies have separate outcome attribution and cooldown histories. A fast loss pauses the fast path without automatically pausing a valid minute setup. Three losses in the same strategy path, playbook, and regime within the configured lookback activate a longer regime-level cooldown.

Alpaca reports one net position per symbol, so several GLD entries appear at the broker as one net quantity with an average entry price. The bot preserves each parent bracket's client order ID as a separate internal trade episode. Every tranche therefore retains its own entry, stop-loss and take-profit legs, fills, outcome, review, supervised label, and journal trail. Multiple bracket legs may fill during the same market move. A new tranche is allowed only when it agrees with the existing GLD direction. An opposite-direction order is blocked because it would reduce or reverse Alpaca's net position instead of creating an independent hedge, which would corrupt per-tranche attribution.

The default learning profile permits five active episodes and no more than $5,000 aggregate GLD notional. A selected exploration probe is capped at $2,000. Unvalidated normal setups remain conservatively sized by the existing risk engine. A new episode waits whenever the concurrent-episode or aggregate-notional limit would be exceeded. Learning mode does not mean a guaranteed trade every second: sampling, signal quality, broker acceptance, position direction, bracket exits, session loss, drawdown, order rate, and execution safety determine the realized frequency.

```dotenv
PAPER_LEARNING_MODE=true
PAPER_LEARNING_FAST_MIN_SCORE=55
PAPER_LEARNING_MIN_SCORE=60
PAPER_LEARNING_MIN_SCORE_GAP=5
PAPER_LEARNING_MAX_NO_TRADE_SCORE=100
PAPER_LEARNING_MIN_PLAYBOOK_SCORE=60
PAPER_LEARNING_MIN_PATTERN_QUALITY=0.45
PAPER_LEARNING_MIN_LIQUIDITY_SCORE=0.40
PAPER_LEARNING_MAX_SPREAD_PCT=0.0015
PAPER_LEARNING_EXPLORATION_MAX_NOTIONAL=2000
PAPER_LEARNING_MAX_CONCURRENT_TRADES=5
PAPER_LEARNING_MAX_AGGREGATE_NOTIONAL=5000
PAPER_LEARNING_FAST_ORDER_COOLDOWN_SECONDS=5
PAPER_LEARNING_STOP_LOSS_PCT=0.0008
PAPER_LEARNING_TAKE_PROFIT_PCT=0.0010
PAPER_LEARNING_IGNORE_MODEL_REJECTION=true
PAPER_LEARNING_EXPLORATION_SAMPLE_RATE=0.25
PAPER_LEARNING_MAX_EXPLORATION_TRADES_PER_DAY=0
PAPER_LEARNING_EXPLORATION_COOLDOWN_SECONDS=5
POST_LOSS_COOLDOWN_SECONDS=120
REGIME_LOSS_LOOKBACK_MINUTES=60
REGIME_LOSS_THRESHOLD=3
REGIME_LOSS_COOLDOWN_MINUTES=30
PERFORMANCE_SNAPSHOT_INTERVAL_SECONDS=60
ESTIMATED_FEE_PER_SHARE=0.001
ESTIMATED_MINIMUM_ORDER_FEE=0
PAPER_LEARNING_MAX_SPREAD_TO_STOP_RATIO=0.65
PAPER_REQUIRE_ML_MODEL=true
PAPER_ENABLE_SHADOW_MODEL=true
PAPER_ML_MIN_ADVISORY_CONFIDENCE=0.45
PAPER_ML_MAX_SCORE_ADJUSTMENT=5
```

##### Exact Two-Lane Decision Order

The bot evaluates each opportunity in this order:

1. Build fresh quote, trade, bar, microstructure, technical, pattern, order-block, options, event, and model features.
2. Run the normal strategy. Nothing about paper exploration lowers normal thresholds.
3. If the normal result is `NO_TRADE`, evaluate whether it is a structured exploration candidate.
4. Apply the exploration thresholds and playbook-specific confirmation rule.
5. Calculate and persist the candidate's selection probability.
6. Deterministically sample the candidate.
7. Run the ordinary entry-quality gate, cooldown policy, risk engine, broker reconciliation, synchronized order coordinator, shortability check, exposure limits, and circuit breakers.
8. Submit an order only when every remaining safety layer passes.

| Requirement | Normal strategy | Paper exploration |
|---|---:|---:|
| Fast score/confidence | `62` / `0.62` | `55` |
| Minute directional score | normal `75`; effectively `88` without a champion | `60` |
| Minute bullish/bearish score gap | normal opposing-score contract | `5` |
| Playbook score | `70` | `60` |
| Pattern quality | `0.58` | `0.45` |
| Liquidity | `0.55` | `0.40` |
| Sampling | none; every fully valid setup proceeds | base `25%`, adaptively capped at `75%` |
| Per-probe notional | normal risk sizing | maximum `$2,000` |
| Daily exploration count | not applicable | `0` means unlimited |

`PAPER_LEARNING_MAX_NO_TRADE_SCORE=100` permits an informative candidate even when the model or normal strategy strongly preferred `NO_TRADE`. It does not submit that candidate automatically. The recognized playbook, reduced quality thresholds, sampling decision, and all hard safety gates must still pass. `PAPER_LEARNING_IGNORE_MODEL_REJECTION=true` applies only after `controlled_exploration_selected=true`; it cannot override a rejection for a normal trade.

##### Playbook-Specific Exploration

Exploration does not use one generic pattern rule:

- `proper_breakout`, `buildup_break`, and `compression_breakout` still require an actual proper break.
- `false_break_reversal` requires a false break that returned into the range; it does not require a proper-break confirmation.
- `pullback_continuation` requires a classified pullback and directional playbook agreement.
- `spread_capture` is fast-path only and retains the normal tight-spread contract.
- `news_event` requires a post-release event, a volatility burst, directional playbook agreement, and all but at most one playbook confirmation.
- Any playbook may miss no more than one declared confirmation in exploration mode.

Low-volatility or sideways regimes can contribute exploration evidence only when these structural rules pass. Poor liquidity and uncontrolled high volatility remain blocked.

##### Unlimited Does Not Mean Uncontrolled

`PAPER_LEARNING_MAX_EXPLORATION_TRADES_PER_DAY=0` disables only the daily exploration count. It does not disable:

- maximum five simultaneous GLD episodes;
- maximum `$5,000` aggregate paper-learning notional;
- five-second exploration and fast-order cooldown;
- maximum 12 orders per minute;
- session-loss and drawdown circuit breakers;
- consecutive-loss and regime cooldowns;
- regular-session and market-close entry freezes;
- one net GLD direction at a time;
- stale quote, stale trade, websocket, spread, reconciliation, duplicate-order, stuck-order, shortability, or execution-error blocks.

The performance report marks its top-line total as combined and separately produces `normal_strategy_summary`, `exploration_summary`, and `by_evidence_lane`. Every exploration order, outcome, review, label, and journal record remains tagged so exploratory failures cannot be presented as normal-strategy performance.

##### Start Paper Trading

Stop offline tree training, Transformer training, and heavy Ollama work first. In PowerShell:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"

.\.venv\Scripts\python.exe -m gld_scalper.main training-loop-status
.\.venv\Scripts\python.exe -m gld_scalper.main transformer-status
.\.venv\Scripts\python.exe -m gld_scalper.main status

.\.venv\Scripts\python.exe -m gld_scalper.main run-paper
```

## Private GitHub Repository Workflow

The canonical source repository is private and owned by
[`Professor-Masha`](https://github.com/Professor-Masha). Git tracks the project
source, tests, documentation, curated `Knowledge` material, and learned model
artifacts. Learned model binaries and research documents use Git LFS.

The repository intentionally excludes `.env`, credentials, SQLite databases,
raw and derived training data, market-data archives, CSV exports, logs,
backups, Ollama weights, installers, virtual environments, generated backtest
trades, and temporary test files. See `SECURITY.md` and `CONTRIBUTING.md` for
the complete boundary.

Enable the repository safety hook once after cloning:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\scripts\install_git_hooks.ps1
```

After every completed and verified bot change:

```powershell
git status --short
git diff --check
git add <changed-files>
git diff --cached
git commit -m "Describe the completed change"
git push origin main
```

Each future change should end with a meaningful commit and a push to `main`.
The completion report should include the commit SHA and the tests that passed.
Never use `git add .` without first checking `git status`, because the local
project contains valuable private training and trading data that must remain
outside GitHub.

Leave that window open. Stop safely with `Ctrl+C`; the shutdown workflow freezes entries, cancels entries, reconciles orders, flattens configured paper exposure, and verifies broker state.

##### Train The Tree Models After The Session

First create matured 1, 3, 5, and 15-minute labels from the saved paper decisions:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"

.\.venv\Scripts\python.exe -m gld_scalper.main label-paper-outcomes `
  --sources signal fast_scalp `
  --limit 100000

.\.venv\Scripts\python.exe -m gld_scalper.main train --lookback-days 90
```

For the resumable multi-playbook historical and paper-data search, use the `train-loop` commands in **Resumable Offline Continual Training**. Training creates candidates; it does not make a candidate a champion unless the promotion rules pass.

##### Train The Transformer After The Session

Build or refresh a scope-specific sequence artifact, then train it:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main build-transformer-dataset `
  --database "D:\ALPACA TEST\gld_scalper_bot\data\paper\gld_scalper.db" `
  --scope minute `
  --source raw `
  --start 2026-01-01 `
  --end 2027-01-01 `
  --max-samples 50000 `
  --max-features 64

.\.venv\Scripts\python.exe -m gld_scalper.main train-transformer `
  --artifact "D:\ALPACA TEST\gld_scalper_bot\data\paper\ml_training\transformer\YOUR_ARTIFACT_FOLDER" `
  --d-model 32 `
  --layers 2 `
  --batch-size 16 `
  --epochs 10 `
  --patience 4 `
  --walk-forward-folds 3 `
  --walk-forward-epochs 2
```

On the 8 GB laptop, use one Transformer scope at a time. The exact continual Transformer commands and graceful-stop procedure are in **Continual Transformer Training**.

##### Use Ollama As An Offline Coach

Ollama does not fit PyTorch or tree-model weights. It reviews stored evidence, suggests advisory labels and training changes, and writes its outputs back to SQLite. Confirm the existing server first:

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

If it is not running, start it in one PowerShell window:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
$env:OLLAMA_MODELS="D:\ALPACA TEST\gld_scalper_bot\_ollama_models"
.\OLLAMA\ollama.exe serve
```

In another PowerShell window:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
$env:LLM_PROVIDER="ollama"
$env:LLM_BASE_URL="http://127.0.0.1:11434"
$env:LLM_MODEL="llama3.2:1b"
$env:LLM_TIMEOUT_SECONDS="240"
$env:ENABLE_LLM_ANALYSIS="true"
$env:ENABLE_LLM_TRAINING_ADVICE="true"
$env:ENABLE_LLM_TRAINING_LABELS="true"
$env:ENABLE_LLM_LIVE_TRADING="false"

.\.venv\Scripts\python.exe -m gld_scalper.main llm-offline-cycle --cadence daily

.\.venv\Scripts\python.exe -m gld_scalper.main llm-analyze `
  --query "Review normal and exploration trades separately. Analyze losses, missed opportunities, setup quality, exit quality, and feature improvements."
```

Keep Ollama outside the live order path. Do not run a heavy Ollama review, tree trainer, or Transformer trainer while `run-paper` is active on the 8 GB laptop.

Dynamic position-management and one-second snapshot settings:

```dotenv
ENABLE_DYNAMIC_POSITION_MANAGEMENT=true
POSITION_MANAGER_INTERVAL_MS=250
POSITION_MANAGER_BROKER_REFRESH_SECONDS=2
PRICE_SNAPSHOT_INTERVAL_SECONDS=1
POSITION_EMERGENCY_STOP_PCT=0.0030
POSITION_BREAKEVEN_TRIGGER_PCT=0.00045
POSITION_BREAKEVEN_OFFSET_PCT=0.00005
POSITION_TRAILING_TRIGGER_PCT=0.00070
POSITION_TRAILING_DISTANCE_PCT=0.00035
POSITION_MIN_STOP_IMPROVEMENT=0.02
POSITION_STOP_REPLACE_COOLDOWN_SECONDS=2
POSITION_PROFITABLE_TIME_EXIT_BUFFER_PCT=0.00010
POSITION_INVALIDATION_MIN_LOSS_PCT=0.00025
POSITION_INVALIDATION_MIN_CONFIDENCE=0.80
POSITION_INVALIDATION_REQUIRED_VOTES=2
POSITION_CLOSE_MANAGEMENT_MINUTES_BEFORE_CLOSE=30
POSITION_CLOSE_RISK_REDUCTION_MINUTES_BEFORE_CLOSE=15
POSITION_FORCE_FLATTEN_MINUTES_BEFORE_CLOSE=8
ENABLE_PARTIAL_PROFIT_TRANCHES=true
PARTIAL_PROFIT_FRACTION=0.50
PARTIAL_PROFIT_RUNNER_TARGET_MULTIPLIER=2.0
```

`POSITION_EMERGENCY_STOP_PCT=0.0030` means 0.30%, not 3%. It is a catastrophic boundary, not a promise that the broker will fill at the exact stop. Position size is still constrained by the configured dollar-risk limit using this wider emergency distance. `PAPER_LEARNING_STOP_LOSS_PCT` remains part of the entry spread-quality check, so the wider emergency stop does not make a poor or expensive entry look acceptable.

Multi-horizon paper-decision labels are controlled separately:

```dotenv
ENABLE_MULTI_HORIZON_OUTCOME_LABELS=true
OUTCOME_LABEL_BATCH_SIZE=5000
OUTCOME_LABEL_MIN_EDGE_PCT=0.0002
OUTCOME_LABEL_SLIPPAGE_PCT=0.0001
OUTCOME_LABEL_SNAPSHOT_TOLERANCE_SECONDS=5
OUTCOME_LABEL_MAX_BAR_GAP_MINUTES=2
```

Every matured minute signal and fast scalp decision receives independent 1, 3, 5, and 15-minute forward-return labels. The labeler first uses `price_snapshots`, which contain the live midpoint or last-trade price captured once per second. When those snapshots do not exist, such as for older paper sessions, it falls back to completed one-minute bar closes. A bar timestamp is treated as the start of its minute, so its close is not available until one minute later. This prevents the training data from seeing a future close at decision time.

Each horizon becomes `long_good`, `short_good`, or `no_trade` only after the raw move is reduced by the observed spread and configured two-sided slippage. Small moves that do not clear both costs and `OUTCOME_LABEL_MIN_EDGE_PCT` remain `no_trade`. The database stores the entry price, price source, all four raw forward returns, all four labels, maximum favorable and adverse excursion, cost estimate, and the originating decision type (`signal` or `fast_scalp`). The scheduled research pass labels up to `OUTCOME_LABEL_BATCH_SIZE` mature decisions every five minutes. It does not train a model inside the live order path.

To backfill labels while paper trading and training are stopped:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\.venv\Scripts\python.exe -m gld_scalper.main label-paper-outcomes `
  --start 2026-07-13 `
  --end 2026-07-14 `
  --limit 100000
```

The command is resumable. Decisions already processed from snapshots or bars are skipped, including partial records near the market close where every future horizon cannot exist. This prevents old partial rows from consuming every future scheduler batch. A decision with no trustworthy entry or future price is stored as an audited `unavailable` marker, not falsely labeled as a winning trade or a valid no-trade. After repairing or importing missing price data, add `--retry-partial` to retry partial and unavailable records that do not yet contain all four horizons. The command only writes labels to the active paper SQLite database; it does not start Alpaca, submit orders, or train a model.

Learning happens in two stages. The outcome review and supervised label are saved immediately after Alpaca confirms that a trade closed. Model weights are never rewritten inside the order path. Scheduled fitting is allowed only inside the configured Eastern Time maintenance window, defaults to 8:00 PM through 8:00 AM, and is refused while an execution episode is open. The trainer consumes accumulated labels, builds immutable candidates, and performs chronological holdout and walk-forward evaluation. A candidate becomes champion only if the strict promotion rules approve it. Both trading paths refresh their model selection every 60 seconds.

`PAPER_REQUIRE_ML_MODEL=true` makes participation enforceable instead of assumed. At startup, `run-paper` must load either a true champion or an eligible paper-shadow candidate. If neither can be loaded, startup raises an error before it can submit an order. An eligible shadow must have a real artifact, enough labeled trades, acceptable inference latency, and completed purged walk-forward folds. The shadow can participate in paper learning, but its registry status remains `candidate`; this does not pretend that an unprofitable model passed promotion. A required-model probe also waits until the saved model feature profile is available; this prevents the fast stream from trading during the brief startup period before minute-level context has warmed up.

Before any future live-money deployment, set `PAPER_LEARNING_MODE=false`, restore conservative retraining hours, and re-enable appropriate frequency, loss, and streak guardrails. The safety validator refuses to activate this profile outside Alpaca paper mode.

### 17. No-Trade Learning

Skipped trades are useful data.

When the bot logs `NO_TRADE`, it stores the reason and the feature snapshot. Later, the missed-opportunity analyzer checks whether price moved cleanly after the skip.

Possible labels:

- `VALID_NO_TRADE`: the skip was reasonable.
- `MISSED_LONG`: price later moved cleanly upward.
- `MISSED_SHORT`: price later moved cleanly downward.

This helps the model learn from both action and restraint.

### 18. Scheduled Retraining

The bot can retrain candidate models on a schedule, but it is intentionally conservative.

Retraining can be skipped if:

- there are not enough labeled samples
- labels contain only one class
- there are no profitable long/short examples
- the candidate does not beat the current champion

The training system does not automatically trust a new model just because it exists. A candidate must pass validation before promotion.

### 19. How The LLM Fits

The LLM is a local Ollama model. It is currently configured as:

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=llama3.2:1b
LLM_BASE_URL=http://localhost:11434
ENABLE_LLM_LIVE_TRADING=false
```

The LLM can read summaries and recent rows from:

- SQLite tables
- CSV exports
- trading journal
- no-trade logs
- missed opportunities
- macro context
- local headline files when available

The LLM can produce:

- data-analysis reviews
- setup-quality notes
- missed-opportunity explanations
- training advice
- feature suggestions
- advisory labels
- macro/sentiment summaries

The LLM cannot:

- submit orders
- change risk limits
- bypass stale-data checks
- promote a model by itself
- guarantee profitable trades
- replace paper-trading evidence

On this 8 GB laptop, the LLM is intentionally configured as an offline research assistant. Larger local models were heavier and less reliable under memory pressure. The smaller model is more practical for generating lightweight summaries and advice.

### 20. The Safest Daily Workflow

For paper trading:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\.venv\Scripts\python.exe -m gld_scalper.main run-paper
```

## Compact Causal Transformer Upgrade

Copyright and trademark notice: this Transformer subsystem and the wider GLD Scalper Bot remain proprietary software of **@Mashcorp**. Installing PyTorch does not change ownership or licensing.

### Architecture

The bot now supports a small numerical time-series Transformer inspired by the causal attention principle in *Attention Is All You Need*. It is not an LLM. It processes ordered market observations and learns which earlier observations are relevant to the current prediction.

| Component | Laptop-bounded design |
|---|---|
| Encoder | Causal `TransformerEncoder` |
| Layers | 2 by default; 3 is optional |
| Model dimension | 48 by default; 32 or 64 is allowed |
| Attention heads | Exactly 4 |
| Feed-forward dimension | 128 by default |
| Dropout | 0.10 by default |
| Data loading | Memory-mapped NumPy arrays and on-demand windows |
| Batch size | 32 by default |
| Runtime artifact | TorchScript; ONNX is optional |
| Live role | Asynchronous `shadow`, `bounded_adviser`, or promoted `paper_champion` |
| Fallback | Existing random forest and deterministic rules |

The causal mask prevents an observation from attending to a later observation. Chronological train, calibration, holdout, and walk-forward partitions prevent future rows from entering earlier training periods. Explicit missing-data, padding, and market-session masks stop the network from treating missing values, padding, or a previous session as current information.

### Independent Models

One model is not shared across all tasks. Each model has its own artifact, feature profile, registry scope, validation history, and champion history.

| Scope | Input | Default sequence | Purpose |
|---|---|---:|---|
| `fast_microstructure` | Quotes and trades aggregated to one second | 120 seconds | Spread capture, fast breaks, imbalance, liquidity collapse, and volatility bursts |
| `minute` | One-minute decisions or bars | 90 minutes | Price action, trends, pullbacks, compression, patterns, and order blocks |
| `news_event` | Event context with price and liquidity | 90 minutes | Post-event price/liquidity reaction; prose is never processed in the order path |
| `exit` | Position state and movement since entry | Up to 15 minutes | Shadow estimates for hold, reduce, and close behavior |

Entry models output `P(LONG)`, `P(SHORT)`, and `P(NO_TRADE)`. The exit model outputs `P(HOLD)`, `P(REDUCE)`, and `P(CLOSE)`. Every model also estimates 1, 3, 5, and 15-minute returns, expected spread/slippage cost, and uncertainty.

### Safety Boundary

The Transformer has no trading-client, order-coordinator, execution-engine, or risk-engine method. Features are copied into a bounded queue. A background CPU thread performs inference and writes the result to `transformer_predictions`. The live path sees only a previously completed cached value. Missing, stale, slow, incompatible, or failed predictions leave the random forest and deterministic controls fully authoritative.

The Transformer starts in shadow mode. Authority can be increased only in paper mode. It can never override stale-data, liquidity, spread, event, risk, shortability, session-close, exposure, circuit-breaker, or execution-safety blocks. It cannot call the broker, cancel protection, or change broker state directly.

### Install PyTorch

Stop paper trading and offline training first:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\.venv\Scripts\python.exe -m pip install -e ".[transformer]"
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__)"
```

TorchScript is always exported. Optional ONNX support is installed separately:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[transformer,onnx]"
```

### Build Sequence Artifacts

Run these jobs after market hours. The historical SQLite database is opened read-only. Artifacts default to `data\paper\ml_training\transformer`.

```powershell
$HistoricalDb = "D:\ALPACA TEST\gld_scalper_bot\data\paper\historical\gld_2021_2026_ai_full\gld_scalper_historical.db"
```

Fast microstructure:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main build-transformer-dataset `
  --database $HistoricalDb `
  --scope fast_microstructure `
  --source raw `
  --start 2021-01-01 `
  --end 2026-01-01 `
  --max-samples 50000 `
  --max-features 64
```

Minute setup:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main build-transformer-dataset `
  --database $HistoricalDb `
  --scope minute `
  --source raw `
  --start 2021-01-01 `
  --end 2026-01-01 `
  --stride 5 `
  --max-samples 50000 `
  --max-features 64
```

News event:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main build-transformer-dataset `
  --database $HistoricalDb `
  --scope news_event `
  --source raw `
  --start 2021-01-01 `
  --end 2026-01-01 `
  --max-samples 50000 `
  --max-features 64
```

Exit models require trustworthy paper position-management events:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main build-transformer-dataset `
  --database "D:\ALPACA TEST\gld_scalper_bot\data\paper\gld_scalper.db" `
  --scope exit `
  --source raw `
  --start 2026-01-01 `
  --end 2027-01-01 `
  --max-samples 50000 `
  --max-features 64
```

Use `--overwrite` only to replace a named artifact directory. It does not delete SQLite paper-trading data.

### Train A Candidate

Use the artifact path printed by the build command:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main train-transformer `
  --artifact "D:\ALPACA TEST\gld_scalper_bot\data\paper\ml_training\transformer\YOUR_ARTIFACT_FOLDER" `
  --d-model 48 `
  --layers 2 `
  --batch-size 32 `
  --epochs 15 `
  --patience 4 `
  --walk-forward-folds 3 `
  --walk-forward-epochs 4
```

For a lower-memory first run, use `--d-model 32 --batch-size 16 --epochs 10`. An interrupted training run creates no approved model. A later run creates a new version; old candidates, champions, manifests, fingerprints, and TorchScript artifacts remain preserved.

The trainer reports chronological holdout and expanding walk-forward results separately. Returns include stored spread/slippage costs. Reports include class balance, calibration, abstention, uncertainty, regimes, profit factor, drawdown, and median/p95 CPU latency. `--export-onnx` is optional after installing ONNX dependencies.

### Baseline And Promotion

Paper decision artifacts record the probabilities and version of the exact random-forest model used for each decision. A Transformer must beat that saved baseline on after-cost return, profit factor, balanced accuracy, and calibration. If exact baseline rows are unavailable, the candidate remains shadow-only.

Promotion also requires completed walk-forward folds, positive after-cost expectancy, enough trades, multiple regimes, bounded drawdown, calibrated abstention, stable inference below the latency ceiling, and sufficient profitable paper results.

After the multi-horizon paper outcome labeler has completed, join shadow predictions to their paper outcomes and update each model version's after-cost evidence:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main evaluate-transformer-paper

# Or evaluate one named version only:
.\.venv\Scripts\python.exe -m gld_scalper.main evaluate-transformer-paper `
  --model-version "transformer-minute-YYYYMMDD-HHMMSS-ffffff"
```

This evaluator calculates paper prediction/trade counts, win rate, profit factor, net return, expectancy, drawdown, accuracy, confidence, and the evaluation horizon. Exit models remain shadow-only until trustworthy exit-specific outcome labels exist.

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main transformer-status

.\.venv\Scripts\python.exe -m gld_scalper.main promote-transformer `
  --model-version "transformer-minute-YYYYMMDD-HHMMSS-ffffff"
```

A failed promotion during early shadow collection is expected. Do not weaken the safety gates simply to create a champion.

### Paper Transformer Runtime

Recommended laptop settings:

```dotenv
ENABLE_TRANSFORMER_SHADOW=true
TRANSFORMER_TRADING_MODE=shadow
TRANSFORMER_QUEUE_SIZE=64
TRANSFORMER_CACHE_MAX_AGE_SECONDS=5
TRANSFORMER_MODEL_REFRESH_SECONDS=60
TRANSFORMER_TORCH_THREADS=2
```

Start the normal bot:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\.venv\Scripts\python.exe -m gld_scalper.main run-paper
```

Transformer results are stored in the paper database and exported under `ml\transformer_predictions`. Paper and future live data remain separate. Ollama and FinGPT remain offline research tools and are not required for Transformer inference.

| Runtime status | Meaning |
|---|---|
| `unavailable` | No completed cached prediction exists yet |
| `no_model` | No candidate/champion exists for that independent scope |
| `shadow` | A fresh advisory prediction was saved |
| `bounded_adviser` | A fresh prediction may slightly reinforce or veto an existing deterministic paper setup; it cannot originate one |
| `paper_champion` | A promoted champion may recommend LONG, SHORT, or NO_TRADE in paper mode when cost, uncertainty, technical confluence, and all later safety gates pass |
| `stale` | Cache age exceeded its limit; random forest/rules remain authoritative |
| `error` | Loading or inference failed; order handling continues without it |

### Resumable Transformer Training Loop

The one-candidate command is useful for diagnosis. The continual loop is the normal after-hours research process. It trains `fast_microstructure`, `minute`, `news_event`, and `exit` as separate registry scopes. You may start with only the scopes for which you have trustworthy labels and add the remaining scopes later.

The loop does the following:

1. Opens each named historical sequence artifact.
2. Rebuilds a bounded paper sequence artifact when the active paper database has at least 30 matching outcome labels.
3. Combines recent paper sequences with historical replay. Historical examples remain in the training set, which reduces catastrophic forgetting.
4. Fingerprints the historical data, paper data, feature profile, architecture, thresholds, sequence length, and seed.
5. Skips an experiment that already completed with the exact same fingerprint and configuration.
6. Warm-starts a previous checkpoint only when feature order, classes, sequence length, and architecture are exactly compatible. Otherwise it deliberately starts fresh.
7. Searches bounded laptop-safe combinations of model dimension, layers, dropout, learning rate, random seed, sequence length, confidence threshold, and probability-margin threshold.
8. Performs a chronological holdout and purged expanding walk-forward test for every candidate.
9. Preserves every checkpoint, TorchScript file, manifest, registry record, lineage pointer, and experiment result. Nothing overwrites a prior candidate or champion.
10. Stops searching after the configured number of rounds without meaningful score improvement. With `--watch`, it then sleeps and resumes only after paper labels create a new dataset fingerprint.

The loop refuses to begin a training cycle during the regular US equity session when `CONTINUAL_TRAINING_ONLY_OUTSIDE_REGULAR_HOURS=true`. This protects the 8 GB laptop's live data and order threads.

Find the artifact directories created earlier:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"

Get-ChildItem ".\data\paper\ml_training\transformer" -Directory |
  Select-Object FullName, LastWriteTime
```

Start with every artifact you actually built. Replace each example folder with the real path shown by PowerShell:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main transformer-train-loop `
  --artifact "fast_microstructure=D:\ALPACA TEST\gld_scalper_bot\data\paper\ml_training\transformer\FAST_ARTIFACT.seq" `
  --artifact "minute=D:\ALPACA TEST\gld_scalper_bot\data\paper\ml_training\transformer\MINUTE_ARTIFACT.seq" `
  --artifact "news_event=D:\ALPACA TEST\gld_scalper_bot\data\paper\ml_training\transformer\NEWS_ARTIFACT.seq" `
  --artifact "exit=D:\ALPACA TEST\gld_scalper_bot\data\paper\ml_training\transformer\EXIT_ARTIFACT.seq" `
  --watch `
  --interval-minutes 60 `
  --epochs 8 `
  --walk-forward-epochs 2 `
  --batch-size 32 `
  --no-improvement-patience 3 `
  --minimum-improvement 0.001 `
  --clear-stop
```

For the first run on an 8 GB laptop, train one scope at a time with `--batch-size 16`. A loop with only the minute artifact is valid:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main transformer-train-loop `
  --artifact "minute=D:\ALPACA TEST\gld_scalper_bot\data\paper\ml_training\transformer\MINUTE_ARTIFACT.seq" `
  --watch `
  --batch-size 16 `
  --clear-stop
```

Expected progress lines include:

```text
paper Transformer artifact scope=minute samples=...
Transformer train scope=minute round=... fingerprint=... warm_start=...
Transformer completed scope=minute candidate=... score=... improved=...
Transformer search converged; ... Watching for a new paper-data fingerprint.
```

Request a clean stop from a second PowerShell window:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\.venv\Scripts\python.exe -m gld_scalper.main stop-transformer-training
```

The runner checks `data\paper\ml_training\transformer\continual\STOP_TRANSFORMER_TRAINING` between experiments and during waits. Restart with `--clear-stop`. Its resumable state is `continual\state.json`; exact experiment history is in SQLite table `transformer_training_experiments`.

### Transformer Authority Graduation

Use one mode at a time in `.env`:

```dotenv
# Stage 1: predictions are recorded but do not alter decisions.
TRANSFORMER_TRADING_MODE=shadow

# Stage 2: may add at most three score points or veto an existing setup.
# It cannot turn NO_TRADE into LONG or SHORT.
# TRANSFORMER_TRADING_MODE=bounded_adviser

# Stage 3: only a registry champion is loaded. It may recommend an action in
# Alpaca paper mode when technical confluence agrees.
# TRANSFORMER_TRADING_MODE=paper_champion
```

Recommended limits:

```dotenv
TRANSFORMER_BOUNDED_MAX_SCORE_ADJUSTMENT=3.0
TRANSFORMER_ADVISER_MIN_CONFIDENCE=0.65
TRANSFORMER_ADVISER_MAX_UNCERTAINTY=0.60
TRANSFORMER_CHAMPION_MIN_EXPECTED_EDGE_PCT=0.00015
TRANSFORMER_AUTO_PROMOTION=true
TRANSFORMER_AUTO_DEMOTION=true
TRANSFORMER_DEMOTION_MIN_PAPER_TRADES=50
TRANSFORMER_DEMOTION_PROFIT_FACTOR=0.85
```

The paper evaluator can automatically promote only after the candidate beats the exact saved random-forest baseline and passes holdout, walk-forward, after-cost, calibration, latency, regime, and minimum paper-trade gates. It automatically archives a paper champion after enough paper evidence if its after-cost profit factor, net return, or drawdown falls outside the configured range. The random forest and deterministic strategy remain available at all times. This upgrade does not authorize live-money Transformer trading.

### Technical Confluence Route

The deterministic route complements the existing price-action playbooks. It does not replace them and no single indicator can trigger an order.

| Family | Examples | Aggregation rule |
|---|---|---|
| Trend | EMA, SMA, VWAP, 5-minute and 15-minute trend | Correlated indicators become one bounded trend-family vote |
| Structure | Support/resistance, buildup/break/pullback, FVG, Fibonacci, order blocks | One bounded structure-family vote |
| Momentum | RSI, confirmed RSI divergence, MACD histogram/slope, ROC | One bounded momentum-family vote |
| Volatility | ATR health, volatility bursts | One volatility-quality vote |
| Volume | Relative volume | One volume-quality vote |
| Microstructure | Quote imbalance, signed volume, aggressive flow, liquidity/spread | One bounded tape vote |
| Events | Scheduled release plus post-release tape confirmation | One bounded event vote |

The “Big 3” are trend, structure, and momentum. Trend-continuation and breakout routes require all three to point the same way. The available routes are trend continuation, mean reversion, FVG retest, breakout/retest, news momentum, and structure reversal. Every evaluation records direction, quality, entry zone, invalidation, stop, target 1, target 2, reward/risk, confidence, reasons, and an abstention reason.

Fibonacci anchors use confirmed, alternating ZigZag-style pivots. Pivot depth defaults to 10 bars and the minimum reversal is scaled by 10-period ATR times a deviation multiplier of 3.0, based on the supplied Auto Fib reference. The engine records retracements `0`, `0.236`, `0.382`, `0.5`, `0.618`, `0.65`, `0.786`, and `1`, plus extensions `1.272`, `1.414`, `1.618`, `1.65`, `2.618`, `3.618`, and `4.236`. It falls back to window extrema only when too few confirmed pivots exist.

RSI divergence uses Wilder RSI(14), 70/30 overbought/oversold zones, a 90-bar lookback, and two right bars to confirm a pivot. Bearish divergence requires a higher price pivot and lower RSI pivot; bullish divergence requires a lower price pivot and higher RSI pivot. This confirmation delay is intentional and prevents a forming pivot from leaking into the current decision.

The FVG engine detects three-candle bullish and bearish imbalances, then records zone boundaries, midpoint, age, touch time, fill fraction, and lifecycle state: `open`, `partially_filled`, `filled`, or `invalidated`. Records are stored in `fair_value_gaps` and exported to `technical_structure\fair_value_gaps.csv`.

Stops and targets can use the confluence route's structure, FVG edge/midpoint, Fibonacci 1.272/1.618 extensions, support/resistance, and ATR buffers. Once a position has sufficient favorable movement, the position manager can trail behind EMA, VWAP, FVG midpoint, or nearby structure. Confirmed opposing confluence, RSI divergence, order blocks, or FVG structure contribute invalidation votes. Liquidity/spread deterioration can close an economically profitable trade, but cannot bypass the emergency and session-close protections.

Scheduled CPI, PPI, PCE, jobs/NFP, FOMC/Fed, GDP, USD/yield, geopolitical, and central-bank-gold context is treated as a prior, not a guaranteed direction. A news-momentum route exists only after release when volatility, liquid spread, and quote/trade pressure confirm the same direction. Conflicting tape produces `NO_TRADE`.

For LLM analysis, preferably after the bot is stopped or outside market hours:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\OLLAMA\ollama.exe serve
```

Then in another PowerShell window:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\.venv\Scripts\python.exe -m gld_scalper.main llm-training-advice
.\.venv\Scripts\python.exe -m gld_scalper.main llm-analyze
```

This keeps live trading responsive and lets the LLM work as a slower after-action reviewer.

## Alpaca API Alignment

The bot follows the Alpaca Python SDK and Trading API behavior used by the current implementation:

- It uses `TradingClient.submit_order(...)` for order creation.
- It uses `TradingClient.get_orders(filter=GetOrdersRequest(...))` to reconcile recent order state.
- It uses `TradingClient.get_order_by_client_id(...)` to make entry retries idempotent.
- It uses `TradingClient.cancel_order_by_id(...)` before flattening or recovering stuck orders.
- It uses `TradingClient.replace_order_by_id(...)` for serialized protected-exit adjustments.
- It uses `TradingClient.close_position(...)` only after GLD orders are canceled, then polls positions and orders until flat.
- It submits bracket orders with `order_class=bracket`, `take_profit`, and `stop_loss`.
- It uses limit entries with `time_in_force=day`, which Alpaca supports for bracket orders.
- It does not enable extended-hours bracket orders, because Alpaca states bracket orders do not support extended hours.
- It uses `StockDataStream.subscribe_bars`, `subscribe_quotes`, and `subscribe_trades` for live market data.
- It uses `OptionHistoricalDataClient.get_option_chain(...)` for bounded GLD option-chain snapshots.
- It defaults to Alpaca's free `indicative` options feed; `opra` must only be selected for an entitled account.
- It treats option snapshots as advisory context and does not place options orders.
- It keeps paper trading enforced with `paper=True` and rejects non-paper endpoints.

Helpful Alpaca references:

- Alpaca paper trading: https://docs.alpaca.markets/docs/paper-trading
- Alpaca orders and bracket orders: https://docs.alpaca.markets/us/docs/orders-at-alpaca
- Alpaca Python SDK order methods: https://alpaca.markets/sdks/python/api_reference/trading/orders.html
- Alpaca Python SDK live stock data: https://alpaca.markets/sdks/python/api_reference/data/stock/live.html
- Alpaca option chain: https://docs.alpaca.markets/us/reference/optionchain
- Alpaca options trading and paper enablement: https://docs.alpaca.markets/us/docs/options-trading

## Project Layout

```text
gld_scalper_bot/
  src/gld_scalper/
    main.py                 CLI entry point and paper loop
    config.py               Environment configuration and paper-safety checks
    database.py             SQLite helper methods
    schema.sql              SQLite table definitions
    data_collector.py       Alpaca historical bar backfill
    stream_collector.py     Alpaca live stock data websocket collector
    order_blocks.py         Confirmed multi-timeframe zones and live retests
    options_intelligence.py Background GLD option-chain intelligence
    outcome_labeler.py      Cost-aware 1/3/5/15-minute paper-decision labels
    macro_context.py        Slow FinGPT-style macro/sentiment context builder
    offline_review.py       Local RAG coach and offline reviewer agents
    rl_environment.py       Offline FinRL-style GLD scalping environment preview
    target_exposure.py      Target exposure interface for strategy/risk separation
    strategy_engine.py      Rule-based LONG/SHORT/NO_TRADE scoring
    risk_engine.py          Risk blocks and order plan sizing
    execution_engine.py     Alpaca bracket order submission
    execution_safety.py     Shared order queue, reconciliation, shutdown, and circuit breaker
    order_reconciler.py     Broker order/fill/outcome sync
    shortability.py         GLD shortability checks
    ml/                     Training, prediction, registry, retraining scheduler
    reports/                CSV exporter and reports
  tests/                    Unit tests
  data/                     SQLite database, ignored by git
  logs/                     Runtime logs, ignored by git
  exports/                  CSV exports, ignored by git
  models/                   Trained model files, ignored by git
  scripts/                  Linux helper scripts
  systemd/                  Example Linux service file
```

## Important Safety Rules

The code enforces these rules before it can trade:

- `ALPACA_PAPER=true`
- `ALPACA_PAPER_TRADE=true`
- `ALPACA_ENDPOINT` must point to Alpaca paper API.
- `BOT_SYMBOL` must be `GLD`.
- The Alpaca `TradingClient` is created with `paper=True`.
- The bot refuses non-GLD trades.
- Shorts require Alpaca asset checks for active, tradable, marginable, and shortable status.
- Broker reconciliation must succeed before a trade can be placed.
- Same-direction paper episodes must remain within concurrent quantity and notional limits.
- An opposite direction requires the controlled flatten-and-reconcile switch.
- Unknown GLD positions or orders freeze entries and trigger residual cleanup.
- All broker writes pass through one idempotent order-intent queue.
- Entries freeze 15 minutes before the regular close and positions flatten 10 minutes before close.
- Repeated broker, stream, reconciliation, or order-state failures latch the circuit breaker.
- Stale market data blocks trading.
- Disconnected or stale live stream blocks trading.
- Wide spread blocks trading.
- The bot can only trade during the configured allowed session.

Default setting is regular-hours only:

```dotenv
ENABLE_EXTENDED_HOURS=false
```

That means after 4:00 PM New York time, the bot should normally log `NO_TRADE` or `market/session not allowed`.

## Windows PowerShell Setup

Open PowerShell and go to the project folder:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
```

Create the virtual environment:

```powershell
python -m venv .venv
```

Upgrade pip:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

Install the bot and development tools:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Check that dependencies are healthy:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

## Environment File

Create or edit `.env` in the project folder. Do not commit this file.

Required Alpaca paper settings:

```dotenv
ALPACA_API_KEY=your_paper_key_here
ALPACA_SECRET_KEY=your_paper_secret_here
ALPACA_PAPER=true
ALPACA_PAPER_TRADE=true
ALPACA_ENDPOINT=https://paper-api.alpaca.markets/v2
ALPACA_DATA_FEED=iex
```

Core bot settings:

```dotenv
BOT_DATA_MODE=paper
DATABASE_URL=sqlite:///data/paper/gld_scalper.db
BOT_SYMBOL=GLD
PAPER_ACCOUNT_SIZE=1000000
MIN_TRADE_NOTIONAL=5000
MAX_TRADE_NOTIONAL=25000
MAX_DAILY_LOSS_PCT=0.01
MAX_TRADE_RISK_PCT=0.0025
MAX_TRADES_PER_DAY=20
MAX_CONSECUTIVE_LOSSES=3
MAX_HOLDING_MINUTES=15
ENABLE_SHORTS=true
ENABLE_EXTENDED_HOURS=false
ENABLE_LIVE_STREAM=true
STREAM_STARTUP_GRACE_SECONDS=20
BAR_STALE_SECONDS=180
QUOTE_STALE_SECONDS=30
STALE_DATA_SECONDS=120
```

Execution-safety settings:

```dotenv
EXECUTION_RECONCILE_INTERVAL_SECONDS=3
EXECUTION_ENTRY_FREEZE_MINUTES_BEFORE_CLOSE=15
EXECUTION_SESSION_FLATTEN_MINUTES_BEFORE_CLOSE=10
EXECUTION_INTENT_TIMEOUT_SECONDS=30
EXECUTION_ORDER_STATE_TIMEOUT_SECONDS=30
EXECUTION_CANCEL_WAIT_SECONDS=15
EXECUTION_SHUTDOWN_TIMEOUT_SECONDS=90
EXECUTION_DIRECTION_SWITCH_COOLDOWN_SECONDS=3
EXECUTION_ENABLE_DIRECTION_SWITCH=true
EXECUTION_FLATTEN_RESIDUAL_POSITIONS=true
EXECUTION_FLATTEN_ON_SHUTDOWN=true
EXECUTION_BROKER_REJECTION_THRESHOLD=3
EXECUTION_STREAM_FAILURE_THRESHOLD=3
EXECUTION_RECONCILIATION_FAILURE_THRESHOLD=3
EXECUTION_ORDER_STATE_FAILURE_THRESHOLD=3
```

Keep the freeze setting between 10 and 15 minutes. The flatten boundary must be less than or equal to the freeze boundary. A short intent timeout does not make Alpaca faster; setting it too low only causes the local caller to stop waiting while the queued operation may still be completing. The defaults favor confirmation over speed for account-state changes.

Fast scalping settings:

```dotenv
ENABLE_FAST_SCALP=true
ENABLE_FAST_SCALP_ORDER_SUBMISSION=true
FAST_SCALP_INTERVAL_MS=250
FAST_SCALP_EVENT_QUEUE_SIZE=5000
FAST_SCALP_MIN_QUOTE_COUNT=3
FAST_SCALP_MIN_TRADE_COUNT=1
FAST_SCALP_LOOKBACK_SECONDS=5
FAST_SCALP_FALSE_BREAK_WINDOW_SECONDS=3
FAST_SCALP_ORDER_COOLDOWN_SECONDS=30
FAST_SCALP_NO_TRADE_LOG_INTERVAL_SECONDS=10
FAST_SCALP_TIGHT_SPREAD_PCT=0.00035
FAST_SCALP_BREAKOUT_MIN_MOVE_PCT=0.00025
FAST_SCALP_IMBALANCE_THRESHOLD=0.30
FAST_SCALP_MIN_TRADE_INTENSITY=0.40
FAST_SCALP_MIN_CONFIDENCE=0.62
FAST_SCALP_VOLATILITY_BURST_PCT=0.0010
FAST_SCALP_MAX_QUOTE_AGE_SECONDS=2
FAST_SCALP_MAX_TRADE_AGE_SECONDS=3
FAST_SCALP_MIN_SPREAD_STABILITY=0.55
MINUTE_ENTRY_MAX_QUOTE_AGE_SECONDS=15
MINUTE_ENTRY_MAX_TRADE_AGE_SECONDS=30
MINUTE_ENTRY_MIN_TRADE_INTENSITY=0.05
MINUTE_ENTRY_MIN_SPREAD_STABILITY=0.35
```

Controlled paper-learning settings:

```dotenv
PAPER_LEARNING_MODE=true
PAPER_LEARNING_FAST_MIN_SCORE=55
PAPER_LEARNING_MIN_SCORE=60
PAPER_LEARNING_MIN_SCORE_GAP=5
PAPER_LEARNING_MAX_NO_TRADE_SCORE=100
PAPER_LEARNING_MIN_PLAYBOOK_SCORE=60
PAPER_LEARNING_MIN_PATTERN_QUALITY=0.45
PAPER_LEARNING_MIN_LIQUIDITY_SCORE=0.40
PAPER_LEARNING_MAX_SPREAD_PCT=0.0015
PAPER_LEARNING_EXPLORATION_MAX_NOTIONAL=2000
PAPER_LEARNING_MAX_CONCURRENT_TRADES=5
PAPER_LEARNING_MAX_AGGREGATE_NOTIONAL=5000
PAPER_LEARNING_FAST_ORDER_COOLDOWN_SECONDS=5
PAPER_LEARNING_STOP_LOSS_PCT=0.0008
PAPER_LEARNING_TAKE_PROFIT_PCT=0.0010
PAPER_LEARNING_IGNORE_MODEL_REJECTION=true
PAPER_LEARNING_EXPLORATION_SAMPLE_RATE=0.25
PAPER_LEARNING_MAX_EXPLORATION_TRADES_PER_DAY=0
PAPER_LEARNING_EXPLORATION_COOLDOWN_SECONDS=5
POST_LOSS_COOLDOWN_SECONDS=120
REGIME_LOSS_LOOKBACK_MINUTES=60
REGIME_LOSS_THRESHOLD=3
REGIME_LOSS_COOLDOWN_MINUTES=30
PERFORMANCE_SNAPSHOT_INTERVAL_SECONDS=60
ESTIMATED_FEE_PER_SHARE=0.001
ESTIMATED_MINIMUM_ORDER_FEE=0
```

Order-block settings:

```dotenv
ENABLE_ORDER_BLOCKS=true
ORDER_BLOCK_TIMEFRAMES=1 5 15 30 45 60
ORDER_BLOCK_HISTORY_MINUTES=3900
ORDER_BLOCK_LOOKBACK_BARS=80
ORDER_BLOCK_DISPLACEMENT_ATR=1.20
ORDER_BLOCK_MIN_VOLUME_RATIO=1.05
ORDER_BLOCK_MAX_AGE_BARS=120
ORDER_BLOCK_RETEST_TOLERANCE_PCT=0.0005
```

`ORDER_BLOCK_HISTORY_MINUTES=3900` gives the detector enough recent one-minute history to form useful 30, 45, and 60-minute candles. Raising it increases SQLite reads; lowering it too far starves the higher timeframes.

GLD options-intelligence settings:

```dotenv
ENABLE_OPTIONS_INTELLIGENCE=true
OPTIONS_UNDERLYING=GLD
OPTIONS_FEED=indicative
OPTIONS_POLL_INTERVAL_SECONDS=30
OPTIONS_EXPIRATION_DAYS=30
OPTIONS_STRIKE_WINDOW_PCT=0.05
OPTIONS_MAX_CONTRACTS=80
OPTIONS_MAX_QUOTE_AGE_SECONDS=180
OPTIONS_MAX_SPREAD_PCT=0.30
OPTIONS_MAX_SCORE_ADJUSTMENT=3.0
```

Keep `OPTIONS_UNDERLYING=GLD`; the safety validator rejects another underlying. Keep the poll interval at 10 seconds or more. With the default `indicative` feed, do not interpret the output as exchange-grade real-time OPRA flow.

Retraining settings:

```dotenv
ENABLE_SCHEDULED_RETRAINING=true
RETRAIN_INTERVAL_HOURS=1
RETRAIN_LOOKBACK_DAYS=90
RETRAIN_MIN_SAMPLES=50
RETRAIN_ONLY_OUTSIDE_REGULAR_HOURS=false
ENABLE_LATENCY_AWARE_ML=true
ML_MAX_INFERENCE_LATENCY_MS=5
```

The one-hour/in-session retraining schedule is intended only for the active paper-learning phase. It runs in a background thread and uses one CPU worker for the heavier tree models. Promotion remains validation-gated. For normal conservative paper operation, use `RETRAIN_INTERVAL_HOURS=24` and `RETRAIN_ONLY_OUTSIDE_REGULAR_HOURS=true`.

CSV settings:

```dotenv
ENABLE_HOURLY_CSV_EXPORT=true
CSV_EXPORT_INTERVAL_MINUTES=60
CSV_EXPORT_DIR=exports/paper/hourly
```

Macro and local review settings:

```dotenv
ENABLE_MACRO_CONTEXT=true
MACRO_CONTEXT_INTERVAL_MINUTES=60
MACRO_CONTEXT_MAX_AGE_MINUTES=1440
MACRO_CONTEXT_HEADLINES_PATH=data/macro_headlines.csv
MACRO_CONTEXT_MAX_LIVE_SCORE_ADJUSTMENT=3
ENABLE_MISSED_OPPORTUNITY_LEARNING=true
MISSED_OPPORTUNITY_HORIZON_MINUTES=15
MISSED_OPPORTUNITY_MIN_MOVE_PCT=0.002
MISSED_OPPORTUNITY_MAX_ADVERSE_PCT=0.0012
```

Ollama local LLM settings:

```dotenv
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434
LLM_MODEL=llama3.2:1b
LLM_TIMEOUT_SECONDS=240
LLM_TEMPERATURE=0.1
LLM_MAX_CONTEXT_ROWS=20
ENABLE_LLM_ANALYSIS=true
ENABLE_LLM_MACRO_CONTEXT=false
ENABLE_LLM_REVIEW_COACH=true
ENABLE_LLM_TRAINING_ADVICE=true
ENABLE_LLM_TRAINING_LABELS=false
LLM_TRAINING_LABEL_MIN_CONFIDENCE=0.70
ENABLE_LLM_LIVE_TRADING=false
```

Important: `ENABLE_LLM_LIVE_TRADING` must stay `false`. The local LLM is an analyst, reviewer, label helper, and training assistant. It is not allowed to directly place trades or bypass the risk engine.

`MACRO_CONTEXT_HEADLINES_PATH` is optional. If the file does not exist, the bot still builds macro context from local market proxies such as GLD, UUP, SPY, and TLT bars already stored in SQLite.

Optional headline CSV format:

```csv
timestamp,source,headline,url
2026-01-02T14:00:00+00:00,manual,Gold rally as weaker dollar and rate cut hopes lift safe haven demand,
```

## Ollama Local LLM Setup

The bot can connect to a local Ollama model. This avoids paid API calls and keeps data on your machine, but it still uses your computer's CPU/GPU/RAM.

The LLM is not required for the bot to paper trade. The trading loop can run without Ollama. Ollama is used when you explicitly run LLM commands, or when an LLM feature flag is enabled.

On an 8 GB RAM laptop, keep the LLM model small. The recommended default is:

```dotenv
LLM_MODEL=llama3.2:1b
```

This is less powerful than larger models, but it is much more practical for a small laptop. It can still summarize logs, review recent decisions, and produce simple training advice. Larger models may be smarter but can load slowly, time out, or trigger memory pressure.

Install Ollama from:

```text
https://ollama.com/
```

After installing, open PowerShell and pull a model:

```powershell
ollama pull llama3.2:1b
```

Check that Ollama is running:

```powershell
ollama list
```

The bot talks to Ollama at:

```text
http://localhost:11434
```

That address is local to your computer. No remote OpenAI, Anthropic, or paid LLM API is required for the current setup.

Recommended first model:

```dotenv
LLM_MODEL=llama3.2:1b
```

If your computer has more free RAM and you want stronger analysis, you can try the larger model:

```powershell
ollama pull llama3.2:3b
```

Then set:

```dotenv
LLM_MODEL=llama3.2:3b
```

What the local LLM can do:

- Analyze SQLite data and latest CSV exports.
- Review the trading journal.
- Explain missed opportunities.
- Score setup quality in plain language.
- Suggest feature improvements.
- Generate structured ML training advice.
- Suggest advisory labels for ML candidates.
- Produce macro/sentiment summaries from local headlines.

What the local LLM cannot do:

- It cannot submit orders.
- It cannot override the risk engine.
- It cannot promote a model without the existing validation checks.
- It cannot guarantee better trades.

The LLM can make the research loop smarter, but the bot still needs enough paper-trading data and clean labels before ML quality can improve.

### Recommended LLM Workflow

Use the LLM as an after-action reviewer.

During market hours, the safest approach on an 8 GB laptop is:

- run the trading bot
- keep heavy LLM work stopped
- watch logs and CSV exports

After market hours, or when the bot is stopped:

1. Start Ollama:

```powershell
.\OLLAMA\ollama.exe serve
```

2. Ask the bot for training advice:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main llm-training-advice
```

3. Ask the bot for a broader data review:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main llm-analyze
```

The output is printed to PowerShell and also saved into SQLite.

### Why `ENABLE_LLM_LIVE_TRADING=false`

This setting is deliberately disabled:

```dotenv
ENABLE_LLM_LIVE_TRADING=false
```

The LLM is slower and less deterministic than the strategy and risk code. It may also give weak or generic output when the model is small. For that reason, the LLM should not sit between signal generation and order execution.

The correct design is:

```text
price action + indicators + risk engine -> live trading decision
SQLite + journal + exports -> LLM review later
```

This keeps the live bot safer and keeps the LLM useful as a research tool.

## First-Time Database Setup

Initialize SQLite:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main init-db
```

Optional manual historical backfill:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main backfill --symbols GLD IAU SLV GDX UUP TLT SPY QQQ --days 90
```

The bot also performs startup recovery/backfill automatically when `run-paper` starts.

## Fresh Start Is Optional

Do not reset the bot before every paper-trading session. Paper-trading data is valuable training evidence. The bars, quotes, trades, signals, no-trade logs, fills, trading journal, missed opportunities, and model records are what the bot later uses to evaluate and improve itself.

Use reset only when you deliberately want to delete the active local dataset, for example after a bad test run, a broken schema experiment, or a one-time cleanup:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main reset-data --yes --clear-exports --clear-logs
```

This clears the active SQLite rows, active CSV export folder, active latest export folder, and `logs\bot.log`. With the normal paper configuration, that means it clears `data\paper\gld_scalper.db` records and `exports\paper\...` exports. It does not reset your Alpaca paper account in Alpaca's dashboard.

## Start Paper Trading On PowerShell

Start the bot:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\.venv\Scripts\python.exe -m gld_scalper.main run-paper
```

Open another PowerShell window to watch logs:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
Get-Content .\logs\bot.log -Wait
```

Stop the bot:

```powershell
Ctrl+C
```

## Useful Commands

Show local bot status:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main status
```

Run one local evaluation loop for smoke testing:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main run-paper --once
```

Run without stream and retraining for local code testing:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main run-paper --once --no-stream --no-retraining
```

Create a manual full CSV export:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main export-csv
```

Build and save the current slow macro/sentiment context:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main update-macro-context
```

Run the local RAG-style bot coach over the `Knowledge` folder, SQLite tables, and latest CSV exports:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main review-coach
```

Use Ollama to analyze SQLite data and latest CSV exports:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main llm-analyze
```

Use Ollama to produce macro/sentiment context from local headlines and market proxies:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main llm-macro-context
```

Use Ollama to generate structured ML training advice:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main llm-training-advice
```

Use Ollama to suggest advisory labels for recent signals:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main llm-label-signals --limit 25
```

Train a candidate model while allowing high-confidence LLM labels:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main llm-train-candidate --label-limit 25 --with-advice
```

Run the offline FinRL-style GLD scalping environment preview:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main rl-preview
```

Run the RL preview for a date window:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main rl-preview --start 2026-01-01 --end 2026-06-01
```

Run tests:

```powershell
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall src tests
```

## What The Logs Mean

Common startup messages:

```text
starting paper bot
waiting up to 20 seconds for live stream warmup
started data stream
connected to wss://stream.data.alpaca.markets/v2/iex
subscribed to trades/quotes/bars
```

Common no-trade reasons:

- `market data stale`: latest bars/quotes/trades are too old.
- `websocket disconnected`: live data connection is not healthy.
- `market/session not allowed`: outside regular market hours while extended hours are disabled.
- `spread too wide`: bid/ask spread is above the configured threshold.
- `open GLD position already exists`: bot found an active GLD position.
- `unexpected open orders exist`: broker reports open GLD orders already.
- `no champion model and rule score is not extreme`: rule setup is not strong enough without a trained model.

Common retraining messages:

- `scheduled retraining started`
- `scheduled retraining result status=skipped reason=not enough labeled samples`
- `scheduled retraining result status=skipped reason=training labels need at least two classes`
- `champion model reloaded`

Common slow-context messages:

- `macro context updated`: hourly macro/sentiment context was saved to SQLite.
- `missed-opportunity review completed`: old no-trade signals were labeled as valid skips or missed opportunities.
- `trade review completed`: a closed trade was analyzed and saved as a good-trade or mistake lesson.
- `csv export completed`: hourly CSV files were written.

Skipping is safe. It means the bot does not yet have enough useful labeled outcomes.

## SQLite Data

SQLite database path:

```text
data/paper/gld_scalper.db
```

The database path comes from `DATABASE_URL`. For paper trading, keep it pointed at `sqlite:///data/paper/gld_scalper.db`. For a future live-trading version, use a separate live database such as `sqlite:///data/live/gld_scalper.db` only after the live-trading safety work has been completed.

Important tables:

- `bars`: historical and live minute bars.
- `quotes`: live GLD quote snapshots.
- `market_trades`: live GLD trade prints.
- `signals`: every strategy decision.
- `no_trade_logs`: detailed reasons for no-trade decisions.
- `model_predictions`: one minute-level model output per evaluated minute, including the exact candidate/champion version and its feature snapshot.
- `orders`: submitted and reconciled Alpaca paper orders.
- `account_snapshots`: startup, minute, end-session, and shutdown account equity, cash, buying power, realized/unrealized P/L, and drawdown.
- `fills`: filled Alpaca paper orders with decision quote, expected/submitted/fill price, spread, slippage, fee, and estimated live-cost attribution.
- `trade_outcomes`: completed paper or backtest tranches with root episode, strategy path, playbook, regime, ML context, gross P/L, costs, and net P/L.
- `trade_reviews`: one post-trade lesson per completed outcome, including setup quality, reward, MFE, MAE, mistake category, contributing factors, and counterfactual.
- `trading_journal`: readable trade decision journal.
- `missed_opportunities`: labels no-trade signals as valid skips or missed long/short opportunities.
- `macro_context`: slow FinGPT-style macro/sentiment context.
- `news_items`: optional headline rows and local keyword sentiment scores.
- `order_block_zones`: confirmed zones, strength, retests, mitigation, and invalidation.
- `option_snapshots`: normalized GLD option-chain contract snapshots.
- `options_intelligence`: aggregate options bias, IV, activity proxy, freshness, liquidity, and event risk.
- `llm_reviews`: local RAG coach and offline reviewer-agent output.
- `llm_training_advice`: Ollama-generated structured ML training advice.
- `llm_signal_labels`: Ollama-suggested advisory labels for recent signals.
- `rl_experiments`: offline FinRL-style policy preview results.
- `model_versions`: candidate/champion model registry.
- `system_logs`: structured runtime logs.
- `performance_consistency_audits`: end-session and shutdown checks for unmatched fills, orphan orders, open episodes, and database/broker differences.

All timestamps are stored in UTC.

## Trading Journal

The trading journal is designed for human review. It records the bot's trade-related decision making, not every no-trade minute.

Journal event types:

- `TRADE_DECISION`: strategy, risk, and model checks allowed an order plan.
- `ORDER_SUBMITTED`: Alpaca accepted the paper order.
- `ORDER_SUBMIT_FAILED`: Alpaca or the network rejected submission.
- `ORDER_FILLED`: Alpaca reports a fill for the entry or exit order.
- `TRADE_CLOSED`: a bracket exit filled and the bot calculated the completed trade outcome.
- `TRADE_REVIEW`: the bot analyzed the completed result, classified the lesson, and created a training label.

Dynamic management actions are stored in the linked `position_management_events` table. Important actions include `STOP_REPLACED`, `STOP_REPLACE_FAILED`, `PROTECTED_EXIT_REQUESTED`, and `PROTECTED_EXIT_FAILED`. The final `TRADE_CLOSED` reason is reconciled back to the management event so a trailing-stop exit is not mislabeled as an unexplained fixed stop loss.

The journal includes:

- decision: `LONG` or `SHORT`
- confidence
- bullish score
- bearish score
- no-trade score
- regime
- human-readable reason
- model version and prediction
- model probabilities
- order id and client order id
- side, quantity, price, notional
- status
- P/L when known
- good-trade or mistake label, learning reward, MFE, MAE, and mistake category in the linked trade review
- pattern classification and pattern quality
- liquidity score and volatility regime
- macro bias and macro confidence
- target exposure percentage
- order-block direction, timeframe, strength, and live retest state
- options bias, confidence, bounded score adjustment, and event risk
- reasoning-agent JSON
- feature snapshot JSON
- broker snapshot JSON

CSV location:

```text
exports/paper/latest/trading_journal.csv
```

Hourly timestamped copies:

```text
exports/paper/hourly/export_YYYYMMDD_HHMMSS/trading_journal.csv
```

## CSV Exports

When `ENABLE_HOURLY_CSV_EXPORT=true`, the bot waits one full interval after startup and then exports SQLite rows collected during that interval.

Newest easy-access folder:

```text
exports/paper/latest/
```

Timestamped archive folder:

```text
exports/paper/hourly/export_YYYYMMDD_HHMMSS/
```

Each export includes:

- `bars.csv`
- `quotes.csv`
- `market_trades.csv`
- `orders.csv`
- `fills.csv`
- `trade_outcomes.csv`
- `trade_reviews.csv`
- `trading_journal.csv`
- `missed_opportunities.csv`
- `macro_context.csv`
- `news_items.csv`
- `llm_reviews.csv`
- `llm_training_advice.csv`
- `llm_signal_labels.csv`
- `rl_experiments.csv`
- `signals.csv`
- `no_trade_logs.csv`
- `model_predictions.csv`
- `model_versions.csv`
- `price_snapshots.csv`
- `position_management_state.csv`
- `position_management_events.csv`
- `order_block_zones.csv`
- `option_snapshots.csv`
- `options_intelligence.csv`
- `system_logs.csv`
- `manifest.csv`

`manifest.csv` shows the row count for each exported table and the export window.

The same files also receive organized copies at `patterns/order_block_zones/order_block_zones.csv`, `options/option_snapshots/option_snapshots.csv`, `options/options_intelligence/options_intelligence.csv`, and `learning/trade_reviews/trade_reviews.csv` inside each export folder.

Manual export writes the full current database:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main export-csv
```

## FinGPT-Inspired Slow Macro Context

The bot now includes a local FinGPT-style macro and sentiment layer. This layer is deliberately slow moving. It is not allowed to submit orders, override price action, or force trades.

What it produces:

- `gold_news_sentiment`
- `usd_sentiment`
- `fed_rate_sentiment`
- `risk_off_sentiment`
- `headline_event_risk`
- `sentiment_alignment`
- `macro_bias`
- `macro_confidence`
- positive developments
- potential concerns
- forecast summary

Possible `macro_bias` values:

- `bullish_gold_environment`
- `bearish_gold_environment`
- `event_risk_environment`
- `neutral_environment`

How it works:

1. It reads optional local headline rows from `data/macro_headlines.csv`.
2. It scores headlines with finance-specific keyword rules.
3. It blends those scores with local market proxies from SQLite, such as GLD, UUP, SPY, and TLT bars.
4. It saves one context record to `macro_context`.
5. The live loop reads the latest non-stale macro context and adds it to the feature snapshot.

How it affects trading:

- A bullish GLD macro backdrop can add only a tiny score boost to long setups.
- A bearish GLD macro backdrop can add only a tiny score boost to short setups.
- Event risk can slightly increase the no-trade score and reduce sizing.
- Macro context cannot create a trade by itself.
- Price action, live data freshness, spread, liquidity, risk, broker reconciliation, and model checks still control the final decision.

Run it manually:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main update-macro-context
```

During `run-paper`, the bot refreshes macro context every `MACRO_CONTEXT_INTERVAL_MINUTES` when `ENABLE_MACRO_CONTEXT=true`.

## Local RAG Coach

The local RAG coach is inspired by FinGPT_RAG and MultiAgentsRAG, but it does not call a hosted LLM. It is a deterministic offline reviewer that retrieves evidence from local files and bot data, then writes a structured review.

Evidence sources:

- `Knowledge/` documents
- `trading_journal`
- `no_trade_logs`
- `missed_opportunities`
- `macro_context`
- `rl_experiments`
- `exports/paper/latest/*.csv`

Offline reviewer agents:

- `BullCaseAgent`: looks for what is working.
- `BearCaseAgent`: argues what may be weak or overfit.
- `RiskCriticAgent`: checks risk, event risk, and missed-opportunity labels.
- `ExecutionCriticAgent`: checks order submission and reconciliation concerns.
- `JournalReviewerAgent`: checks whether journal fields are useful for human review.

Run it:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main review-coach
```

Output is saved to:

```text
llm_reviews
exports/paper/latest/llm_reviews.csv
```

This is a coaching and analysis tool. It is not part of the live order path.

## FinRL-Style Offline RL Preview

The bot now has a small offline GLD scalping environment inspired by FinRL/FinRL-X ideas. It is intentionally not a live trading policy.

Environment shape:

- Observation source: SQLite GLD bars.
- Action space: `NO_TRADE`, `LONG`, `SHORT`.
- Size support: each action can carry a `size_multiplier`.
- Reward: directional return minus spread penalty and volatility/risk penalty.
- Promotion gate: no RL policy is promoted unless a future walk-forward test beats current rules after costs and risk penalties.

Run a preview:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main rl-preview
```

Run a date range:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main rl-preview --start 2026-01-01 --end 2026-06-01
```

Results are saved to:

```text
rl_experiments
exports/paper/latest/rl_experiments.csv
```

Current behavior is preview only. It does not train PPO, SAC, TD3, DDPG, or A2C yet, and it does not change live trading. It gives us the environment and persistence layer needed to safely experiment later.

## Target Exposure Interface

FinRL-X-style architecture separates strategy intent from execution. This bot now records a target exposure suggestion for each signal:

```text
NO_TRADE -> 0% GLD exposure
LONG     -> positive GLD exposure
SHORT    -> negative GLD exposure
```

The target exposure is based on:

- signal confidence
- pattern quality
- liquidity score
- volatility burst state
- macro alignment
- event risk

The current execution engine still submits bracket orders through Alpaca only after all existing risk checks pass. Target exposure is an architectural bridge for cleaner future backtests and position-sizing research.

## Machine Learning Behavior

The machine learning model is not allowed to rewrite code or change risk limits. It is a predictive trade-quality assistant.

The model has three runtime roles:

- `champion`: passed the strict promotion rules and may be used by conservative paper trading. A future live-money version must require this role.
- `paper_shadow`: the best eligible unpromoted candidate available for paper-learning advice. It remains a candidate and is never represented as a champion.
- `none`: no model loaded; prediction falls back to `rule_only`. With `PAPER_REQUIRE_ML_MODEL=true`, `run-paper` refuses to start in this state.

Run `status` before paper trading. These fields provide the proof:

```text
ml_participating: true
active_model_role: paper_shadow | champion
active_model_version: candidate-... | champion version
champion_model_version: null | champion version
```

The minute loop runs ML before the final rule decision and inserts a row into `model_predictions` even when the result is `NO_TRADE`. The sub-second path runs the same loaded model for each evaluated fast decision and stores its output in `fast_scalp_decisions`. A paper shadow may add only a bounded score adjustment when its direction agrees with an already valid fast setup. It cannot reverse a fast decision, change a hard-blocked decision, or override clean-breakout, false-break, spread-capture, or news-event confirmation.

This is live inference and evidence collection, not online fitting. The model does not refit itself in the milliseconds between a quote and an order. Closed outcomes become labels, scheduled retraining builds a new immutable candidate in the background, and the runtime later reloads the selected model. That separation keeps slow training and Ollama work out of the order path.

The supervised learning target is still:

```text
long_good
short_good
no_trade
```

Order-block and options numeric values are saved inside each decision feature snapshot. The dynamic paper-training dataset builder can use them after enough labeled paper samples exist. A previously trained champion keeps its exact saved feature profile and ignores unfamiliar extra fields until a new candidate is trained and passes the normal holdout, walk-forward, latency, profitability, and promotion gates. This prevents a source-code upgrade from silently changing an already saved model.

The trainer now uses a latency-aware supervised model search. Instead of always training only one Random Forest, it trains and compares:

- logistic regression
- Gaussian Naive Bayes
- Random Forest
- Gradient Boosting

For each candidate, it records:

- balanced accuracy, precision, recall, and macro F1
- no-trade recall and trade coverage
- probability log loss, Brier score, and expected calibration error
- realized after-cost profit factor, expectancy, win rate, and net return
- maximum drawdown calculated from the predicted trade sequence
- average prediction latency in milliseconds
- latency-aware selection score

The financial metrics are calculated from the model's out-of-sample predictions and the corresponding forward returns. They are not estimated from the number of profitable labels. The selected model is saved as a candidate under `models/paper/`. This lets the bot prefer a fast model when it is accurate enough, which matters for the event-driven fast scalping path.

Important latency settings:

```dotenv
ENABLE_LATENCY_AWARE_ML=true
ML_MAX_INFERENCE_LATENCY_MS=5
ML_VALIDATION_FRACTION=0.20
ML_PURGE_MINUTES=30
ML_EMBARGO_MINUTES=30
ML_MIN_CONFIDENCE=0.58
ML_MIN_PROBABILITY_MARGIN=0.08
ML_MAX_MISSING_FEATURE_FRACTION=0.25
ML_MAX_OUTLIER_FEATURE_FRACTION=0.15
```

For rookie developers: training can take seconds or minutes, but live inference must be tiny. The live bot loads the champion model into memory once. During trading, the model only receives a numeric feature vector and returns class probabilities.

Scheduled retraining:

- runs in a background thread
- uses a separate SQLite connection
- waits for enough labeled samples
- requires at least two label classes
- requires at least one profitable long/short example
- trains a candidate model
- promotes it only if validation metrics beat the current champion
- reloads the champion in the live loop after promotion

### Historical Archive Training Pipeline

The live paper database and the downloaded historical archive have different jobs:

- The historical archive is immutable research input. The ML builder opens it read-only.
- The generated `.joblib` artifact contains feature rows and labels used for repeatable training.
- The normal paper database stores the candidate/champion registry, paper predictions, drift reports, journals, and later paper outcomes.
- Raw historical quotes, trades, bars, news, events, and macro rows are never deleted or rewritten by model training.

The archive builder creates one supervised row at each configured sampling interval. At timestamp `T`, it may use only information available at or before `T`:

- recent GLD price, range, volume, VWAP, breakout, and compression features
- the latest eligible bid/ask quote and quote imbalance
- aligned related-asset returns
- news received before the decision
- the previous or next scheduled economic event
- macro values whose real-time availability date is not later than the decision
- intraday time and weekday features

Future GLD bars are used only to create the answer label. They are never included in the feature vector. Each candidate action is charged the observed spread plus configured slippage. A small raw move that does not clear those costs becomes `no_trade`.

Build the five-year training artifact from the completed archive:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"

.\.venv\Scripts\python.exe -m gld_scalper.main build-ml-archive `
  --database "D:\ALPACA TEST\gld_scalper_bot\data\paper\historical\gld_2021_2026_ai_full\gld_scalper_historical.db" `
  --start 2021-01-01 `
  --end 2026-01-01 `
  --artifact-name gld_2021_2026_training_v3 `
  --stride-minutes 5 `
  --horizon-minutes 5 `
  --horizons 1 3 5 15 `
  --slippage-pct 0.0001 `
  --minimum-edge-pct 0.0002
```

The resulting artifact is stored at:

```text
data/paper/ml_training/gld_2021_2026_training_v3.joblib
```

For a quick pipeline test before building every row, add `--max-samples 5000`. Remove that option for the real training artifact.

Train and validate a candidate from the artifact:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main train-archive `
  --artifact "D:\ALPACA TEST\gld_scalper_bot\data\paper\ml_training\gld_2021_2026_training_v3.joblib"
```

Artifact format v3 stores 1-, 3-, 5-, and 15-minute outcomes for every timestamp and assigns a deterministic research playbook. The older v2 artifact remains readable for its original 5-minute global experiment, but it cannot provide the complete playbook/multi-horizon experiment matrix. Rebuild v3 before starting continual training.

The trainer performs these operations in order:

1. Loads chronological, cost-aware training records.
2. Holds out the newest section for final validation.
3. Removes the purge window immediately before validation so overlapping outcomes cannot leak across the boundary.
4. Reserves a chronological calibration tail inside the training period.
5. Trains fast-feature and full-context versions of Logistic Regression, Gaussian Naive Bayes, Random Forest, and Gradient Boosting.
6. Calibrates probabilities using data later than the model-fitting portion but earlier than final validation.
7. Selects a candidate using classification quality, realized expectancy, profit factor, drawdown, calibration, and latency.
8. Runs purged rolling walk-forward folds across the full archive.
9. Registers the candidate and applies the strict promotion gates.
10. Promotes only when every gate passes; otherwise records the exact rejection reason.

Promotion requires all of the following:

- completed purged walk-forward validation
- the configured minimum number of completed folds and predicted trades
- positive after-cost net return and positive average expectancy
- sufficient profit factor and win rate
- acceptable maximum drawdown
- enough profitable walk-forward folds
- acceptable probability calibration error
- inference latency at or below the configured target
- improvement over the current champion when a champion already exists

Relevant promotion settings:

```dotenv
PROMOTION_MIN_PROFIT_FACTOR=1.20
PROMOTION_MIN_WIN_RATE=0.48
PROMOTION_MAX_DRAWDOWN=0.015
PROMOTION_MIN_TRADE_COUNT=100
PROMOTION_MIN_PROFITABLE_FOLD_RATIO=0.60
PROMOTION_MAX_CALIBRATION_ERROR=0.15
PROMOTION_MIN_FOLD_COUNT=3
```

### Live ML Abstention And Drift

The champion does not have to issue a trade on every input. It returns `NO_TRADE` when:

- the model itself prefers no trade
- confidence is below the configured floor
- the two strongest class probabilities are too close
- required features are missing
- too many features are far outside their training distribution
- the quote or market data is stale
- the websocket reports disconnected
- model inference raises an exception

The model payload stores training means, standard deviations, ranges, and missing rates. Run this command after enough paper predictions have accumulated:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main ml-drift-report --limit 1000
```

The report is saved in SQLite table `model_drift_reports` and included under the `ml` CSV export folder. Status values include `stable`, `watch`, `drifted`, and `demoted`. Drift never promotes or retrains a model. It can automatically demote the affected scope's champion after enough predictions or paper outcomes prove that feature behavior, after-cost profit factor, or drawdown has moved outside the model's validated range. The preserved artifact remains available for audit and rollback.

The fast stream path also uses an asynchronous persistence queue. Fast decisions, no-trade diagnostics, and journal events are written by a dedicated SQLite worker. Prediction and risk checks therefore do not wait for those diagnostic disk writes. Broker order submission and essential order-state persistence remain synchronous because execution state must not be lost.

Alpaca nested bracket responses are treated as a tree. The top-level entry is the trade episode and its `legs` are exits. Child orders do not need to expose a `parent_order_id` field in Alpaca's model: the reconciler derives that relationship from the nested response and saves it explicitly in SQLite. Only the parent receives `position_side`; an exit buy for a short episode is therefore not mistaken for a new long position. This is essential when several same-direction paper brackets are active at once.

Standalone closing legs are handled the same way. A broker order whose `position_intent` ends in `_to_close` is never treated as an opening parent, even if it appears at the top level of a recent-order response. This closes the second path that previously produced false mixed-direction episodes.

### Resumable Offline Continual Training

The continual trainer is an experiment controller, not an infinite call to `fit()` on unchanged data. It remembers completed work in SQLite table `ml_training_experiments` and writes one JSON manifest for every candidate.

An experiment identity includes:

- SHA-256 hash of the v3 archive artifact
- latest paper outcome-label ID and row count
- hash of the ML training/evaluation source files
- playbook and holding horizon
- paper-data weight and minimum-sample policy

If all those inputs are unchanged, the experiment key is unchanged and the trainer skips it. Previous model files, manifests, metrics, thresholds, and failures remain available. When enough new paper labels arrive or ML code changes, the fingerprint changes and a new finite queue is created.

Only matured paper evidence is merged. A raw `NO_TRADE` signal is not automatically treated as a correct no-trade label. It must first receive an outcome label, a missed-opportunity review, a completed trade outcome, or an explicitly enabled high-confidence advisory label. This prevents the model from learning that every unresolved skip was correct.

The loop does not warm-start Random Forest or Gradient Boosting from rejected tree weights. Those estimators are batch models. Its memory is safer and more useful: it preserves prior evidence, prioritizes configurations that performed better before, avoids duplicate work, and compares every new challenger against the same strict gates.

The v3 queue covers these horizons:

```text
1 minute, 3 minutes, 5 minutes, 15 minutes
```

It can evaluate these playbooks:

```text
all
proper_breakout
false_break_reversal
pullback_continuation
compression_breakout
spread_capture
news_event
trend_continuation
```

Playbook-specific candidates are research/shadow candidates. They are never automatically promoted as the global live predictor. The loop sets `allow_promotion=false` for every experiment. A candidate must be reviewed and later prove itself in paper shadow evaluation before any separate promotion decision.

Run one finite experiment cycle first:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"

.\.venv\Scripts\python.exe -m gld_scalper.main train-loop `
  --artifact "D:\ALPACA TEST\gld_scalper_bot\data\paper\ml_training\gld_2021_2026_training_v3.joblib" `
  --horizons 1 3 5 15 `
  --playbooks all proper_breakout false_break_reversal pullback_continuation compression_breakout spread_capture news_event trend_continuation `
  --minimum-samples 750 `
  --paper-weight 2 `
  --clear-stop
```

This runs sequentially and stops when the current finite queue is exhausted. Experiments without enough samples are recorded as skipped. Completed fingerprints are not trained again.

After verifying the first cycle, leave the resumable watcher running:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main train-loop `
  --artifact "D:\ALPACA TEST\gld_scalper_bot\data\paper\ml_training\gld_2021_2026_training_v3.joblib" `
  --watch `
  --interval-minutes 60 `
  --clear-stop
```

Watch mode behaves as follows:

1. It obtains an exclusive training lock.
2. It finishes or skips the current finite experiment queue.
3. It waits without retraining unchanged fingerprints.
4. It checks paper outcome-label growth every configured interval.
5. It starts another cycle only after at least `CONTINUAL_TRAINING_MIN_NEW_LABELS` new labels exist.
6. It refuses to start a watch-mode cycle during regular market hours by default.
7. It saves state after every cycle and resumes after restart.

### Continuous offline historical training

Use this mode before paper trading when you want the laptop to keep searching the saved five-year historical artifact until you stop it. This is different from `--watch`: normal watch mode waits for new matured paper labels, while continuous historical mode immediately advances through numbered search rounds.

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"

.\.venv\Scripts\python.exe -m gld_scalper.main train-loop `
  --artifact "D:\ALPACA TEST\gld_scalper_bot\data\paper\ml_training\gld_2021_2026_training_v3.joblib" `
  --horizons 1 3 5 15 `
  --playbooks all proper_breakout false_break_reversal pullback_continuation compression_breakout spread_capture news_event trend_continuation `
  --minimum-samples 750 `
  --paper-weight 2 `
  --continuous-historical `
  --interval-minutes 1 `
  --patience-rounds 3 `
  --minimum-improvement 0.001 `
  --clear-stop
```

Each search round receives a new persisted round number. The trainer uses that number to vary random seeds and model hyperparameters for Logistic Regression, Random Forest, and Gradient Boosting candidates. The experiment fingerprint includes the round, so completed work is never mistaken for a new run. If Windows restarts or training is interrupted, the same round resumes and completed playbook/horizon experiments are skipped before the loop advances.

Walk-forward validation clones the selected candidate's actual estimator, including its exact hyperparameters, and uses the selected fast or context feature columns in every chronological fold. It no longer evaluates every candidate through a separate fixed Logistic Regression proxy. Candidate manifests record the estimator parameters and report `holdout_metrics` and `walk_forward_metrics` separately. Console output uses explicit names such as `holdout_net_return` and `walk_forward_net_return`.

Continuous mode stops automatically when it converges. `--minimum-improvement 0.001` requires the best actual-candidate walk-forward net return in a round to beat the previous meaningful best by more than `0.001`. `--patience-rounds 3` exits after three consecutive completed rounds that fail that test. The state file preserves the best score and patience counter across restarts. Automatic convergence releases the lock, does not create a stop-request file, and never promotes a model.

This is controlled repeated model selection, not unlimited learning from identical fits. Every candidate remains shadow-only, is evaluated chronologically and with walk-forward testing, and is stored with its metrics. Earlier candidates are remembered and ranked; their results determine experiment priority and provide the previous-best comparison context. Batch tree models do not inherit tree weights from earlier rejected candidates.

The loop prints `search_round=N` on every cycle and `next_search_round=N` before its one-minute rest. It runs during any clock hour because this mode is explicitly for offline use. Do not run `run-paper`, Ollama, or another trainer at the same time on an 8 GB laptop.

Stop it gracefully from a second PowerShell window:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\.venv\Scripts\python.exe -m gld_scalper.main stop-training-loop
```

The active model fit finishes, remaining experiments in that round stop, and the round number is retained for the next start. Before paper trading, confirm that `lock_exists` is `false`:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main training-loop-status
```

Continuous training does not guarantee continuous improvement. Use the recorded walk-forward profit factor, net return, drawdown, trade count, calibration, and inference latency to select a candidate; a later round can be worse than an earlier one. No candidate is automatically promoted or permitted to place orders.

Important settings:

```dotenv
CONTINUAL_TRAINING_HORIZONS=1 3 5 15
CONTINUAL_TRAINING_PLAYBOOKS=all proper_breakout false_break_reversal pullback_continuation compression_breakout spread_capture news_event trend_continuation
CONTINUAL_TRAINING_INTERVAL_MINUTES=60
CONTINUAL_TRAINING_MIN_NEW_LABELS=500
CONTINUAL_TRAINING_MIN_PLAYBOOK_SAMPLES=750
CONTINUAL_TRAINING_PAPER_WEIGHT=2
CONTINUAL_TRAINING_PAPER_LOOKBACK_DAYS=730
CONTINUAL_TRAINING_LOCK_STALE_HOURS=24
CONTINUAL_TRAINING_ONLY_OUTSIDE_REGULAR_HOURS=true
```

Check progress from another PowerShell window:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main training-loop-status
```

Request a graceful stop:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main stop-training-loop
```

The current model fit is allowed to finish, its result is saved, and the loop stops before the next experiment. Use `--clear-stop` the next time you start it.

Continual-training state is stored in:

```text
data/paper/ml_training/continual/state.json
data/paper/ml_training/continual/training.lock
data/paper/ml_training/continual/STOP_TRAINING
```

Candidate manifests are stored in:

```text
models/paper/manifests/
```

On the 8 GB laptop:

- keep the laptop connected to power and disable sleep while the loop runs
- stop Ollama before a large training cycle
- train one model at a time; the loop is sequential
- use `run-paper --no-retraining` when the separate continual trainer is enabled, avoiding two trainers
- do not run a manual one-shot training command while `training.lock` exists
- allow the regular-hours guard to preserve live-stream responsiveness

The bot learns from two kinds of labels:

- completed paper/backtest outcomes in `trade_outcomes`
- matured no-trade reviews in `missed_opportunities`

Missed-opportunity learning checks old `NO_TRADE` signals after the configured horizon. If price later moved cleanly without much adverse movement, the skip can become `MISSED_LONG` or `MISSED_SHORT`. If price did not move cleanly, it becomes `VALID_NO_TRADE`.

The order reconciler records paper fills and completed bracket outcomes so the training dataset can grow from real paper-trading behavior. The missed-opportunity analyzer helps the model learn from skipped setups, not only from submitted trades.

### Ollama-Assisted ML Training

When `LLM_PROVIDER=ollama`, the bot can use a local model to help the research and labeling workflow.

Ollama can:

- review recent signals and journals
- explain why a setup was weak or strong
- suggest feature improvements
- suggest labels for recent unlabeled signals
- generate training advice before building a new candidate model

The important word is **suggest**. The LLM does not become the model, and it does not automatically rewrite the training set. It produces review records that the deterministic training code can optionally use under strict settings.

Suggested labels are saved to:

```text
llm_signal_labels
exports/paper/latest/llm_signal_labels.csv
```

Training advice is saved to:

```text
llm_training_advice
exports/paper/latest/llm_training_advice.csv
```

By default, LLM labels are advisory only:

```dotenv
ENABLE_LLM_TRAINING_LABELS=false
```

To let the trainer consume high-confidence LLM labels, set:

```dotenv
ENABLE_LLM_TRAINING_LABELS=true
LLM_TRAINING_LABEL_MIN_CONFIDENCE=0.70
```

Or run an explicit training command:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main llm-train-candidate --label-limit 25 --with-advice
```

That command does three things:

1. asks Ollama for advisory labels on recent signals
2. asks Ollama for training advice
3. runs the normal candidate-model training process with LLM labels allowed

It still cannot promote a bad model. The same quality gates remain in place:

- enough labeled samples
- at least two label classes
- at least one profitable long/short class
- validation metrics must be acceptable
- candidate must beat the current champion before promotion

Label priority is conservative:

1. Real trade outcomes in `trade_outcomes`
2. Missed-opportunity labels in `missed_opportunities`
3. High-confidence LLM labels in `llm_signal_labels`

That means the LLM can fill gaps, but it does not replace actual paper-trading evidence.

For rookie developers: think of the LLM like a junior analyst reading the journal and saying, "This looks like a missed long," or "This feature might be useful." The trainer may listen only if the advice is high confidence and no better real label exists. The risk engine still has the final word during live trading.

## Backtesting

Run a backtest after you have historical bars:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main backtest --start 2026-01-01 --end 2026-06-01
```

The backtester simulates:

- one-minute loop timing
- next-bar entry
- stop loss
- take profit
- max holding time
- spread and slippage assumptions
- daily loss guard
- consecutive-loss cooldown

Backtest results are saved as `mode=backtest` in `trade_outcomes`.

### Full AI Historical Dataset Download

For the upgraded AI/research workflow, a simple bars-only dataset is not enough. The bot can now download or import a richer standalone dataset into:

```text
data/paper/historical/<dataset-name>/
exports/paper/historical/<dataset-name>/
```

Use this PowerShell command for the full 2021-2026 research dataset:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"

.\.venv\Scripts\python.exe tools\download_historical_data.py `
  --start 2021-01-01 `
  --end 2026-01-01 `
  --dataset-name gld_2021_2026_ai_full `
  --include-adjusted-bars `
  --include-quotes `
  --include-trades `
  --include-news `
  --include-market-calendar `
  --include-event-calendar `
  --include-macro-series `
  --index-knowledge `
  --label-derived `
  --export-csv `
  --max-retries 12 `
  --retry-base-seconds 15
```

Important notes for rookie developers:

- `--include-quotes` and `--include-trades` can create a very large dataset. Keep it on the D: drive.
- `--include-news` depends on your Alpaca account data access.
- `--include-macro-series` needs `FRED_API_KEY` in `.env`.
- `--include-event-calendar` imports a local calendar CSV from `ECONOMIC_CALENDAR_PATH`.
- If a network call times out, run the same command again. The downloader resumes completed chunks unless you pass `--no-resume`.

The exporter now writes both flat CSV files and grouped folders, so each data family is easier to inspect:

```text
exports/paper/historical/<dataset-name>/hourly/<export-run>/market_data/
exports/paper/historical/<dataset-name>/hourly/<export-run>/microstructure/
exports/paper/historical/<dataset-name>/hourly/<export-run>/diagnostics/
exports/paper/historical/<dataset-name>/hourly/<export-run>/calendar/
exports/paper/historical/<dataset-name>/hourly/<export-run>/events/
exports/paper/historical/<dataset-name>/hourly/<export-run>/news/
exports/paper/historical/<dataset-name>/hourly/<export-run>/macro/
exports/paper/historical/<dataset-name>/hourly/<export-run>/strategy/
exports/paper/historical/<dataset-name>/hourly/<export-run>/patterns/
exports/paper/historical/<dataset-name>/hourly/<export-run>/outcomes/
exports/paper/historical/<dataset-name>/hourly/<export-run>/journal/
exports/paper/historical/<dataset-name>/hourly/<export-run>/learning/
exports/paper/historical/<dataset-name>/hourly/<export-run>/ml/
exports/paper/historical/<dataset-name>/hourly/<export-run>/llm/
exports/paper/historical/<dataset-name>/hourly/<export-run>/knowledge/
```

### Upgraded Research Collection Command

During paper trading, the bot can collect slow research data without putting the LLM in the live order path:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main collect-research-data `
  --days 7
```

This command stores:

- Alpaca news records and event labels
- local economic calendar rows
- FRED macro series when configured
- Alpaca market calendar sessions
- Knowledge-folder artifact index rows
- derived outcome labels for trades, no-trades, news, and events

### Walk-Forward Validation

Use walk-forward validation before promoting any ML candidate:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main walk-forward
```

Promotion rules are strict by design. A candidate model should not become champion unless it beats the current rules after realistic costs, has enough trades, avoids excessive drawdown, and passes the configured profit-factor and win-rate floors.

### Final Upgraded Bars-Only Backtest Snapshot

The final upgraded 2021-2026 backtest report was generated at:

```text
reports/backtests/gld_2021_2026_upgraded_final/GLD_Backtest_Report_gld_2021_2026_upgraded_final.docx
```

The generated metrics were:

| Metric | Value |
| --- | ---: |
| Net P/L | -$13,593.53 |
| Total return on $1,000,000 paper equity | -1.36% |
| Trades | 3,643 |
| Win rate | 35.55% |
| Profit factor | 0.542 |
| Max drawdown | 1.36% |
| Average P/L per trade | -$3.73 |
| Long P/L | -$6,270.83 |
| Short P/L | -$7,322.70 |

This is not a profitable promotion result. It is a technical-readiness result: the upgraded code path runs, stores data, exports reports, and passes tests, but the current bars-only historical dataset does not include the real quotes, trades, macro, event calendar, or news fields that the new AI/research layer was built to learn from.

Use the bot in paper trading as a data-collection and model-training system until a richer walk-forward backtest shows durable improvement after spread, slippage, and risk penalties.

## Windows Daily Operating Checklist

Before market open:

1. Confirm the bot is stopped.
2. Preserve the existing paper database; do not reset accumulated training data for a normal restart.
3. Confirm `.env` is paper-only.
4. Start the bot.
5. Watch `logs\bot.log`.
6. Confirm websocket connects.
7. Confirm `execution safety startup complete consistent=True` appears.
8. Confirm the reported GLD position and open-order counts are expected. A residual position may be flattened before startup completes.

During market hours:

1. Let it run.
2. Watch for `broker_order_sync`.
3. Watch for `TRADE_DECISION` and `ORDER_SUBMITTED`.
4. Review `exports\latest\trading_journal.csv`.
5. Treat `CIRCUIT_OPENED`, `execution_safety_shutdown_failed`, or repeated reconciliation errors as a stop-and-investigate condition.

After market close:

1. Review `exports\latest`.
2. Review `trading_journal.csv`.
3. Review `no_trade_logs.csv`.
4. Review `trade_outcomes.csv`.
5. Review `account_snapshots.csv`, `fills.csv`, and `performance_consistency_audits.csv`.
6. Compare `fast` and `minute` rows separately; one path must not hide the other's losses.
7. Let scheduled retraining run if configured outside regular hours.
8. Confirm the log reports a successful flatten, the latest consistency audit passes, and Alpaca shows zero GLD position and zero open GLD orders.

## Phase 4-7 Final Architecture

This section describes the final exit, risk, ML, and FinGPT/Ollama design. It is written as an operating contract: if future code changes contradict this section, stop and review the change before paper trading.

### Phase 4: Cost-Aware Exit Management

An entry price is not breakeven. A trade must first recover execution friction.

The bot calculates economic breakeven as:

```text
current bid/ask spread percentage
+ estimated round-trip slippage percentage
+ estimated round-trip fee percentage
+ safety-buffer percentage
```

The default safety inputs are:

```dotenv
ESTIMATED_ROUND_TRIP_SLIPPAGE_PCT=0.00010
ECONOMIC_BREAKEVEN_SAFETY_BUFFER_PCT=0.00010
ESTIMATED_FEE_PER_SHARE=0.001
```

The dynamic position manager stores the entry-time economic breakeven estimate and recomputes it from the current spread. It uses the larger value. A stop described as a profit lock must therefore lock a price above all estimated round-trip costs, not simply one cent above entry.

Stops are playbook-specific. `spread_capture`, `false_break_reversal`, `proper_breakout`, `compression_breakout`, `buildup_break`, `pullback_continuation`, `trend_continuation`, and `news_event` use different ATR multipliers. The engine uses support, resistance, or the broken range when that structure is close enough to be meaningful. A session-wide level far outside the scalp's risk envelope is recorded as context and replaced by the playbook ATR stop. A setup is rejected when the resulting normal stop exceeds `POSITION_MAX_NORMAL_STOP_PCT`.

The normal stop and broker emergency stop are different:

```dotenv
POSITION_MAX_NORMAL_STOP_PCT=0.0020
POSITION_EMERGENCY_STOP_PCT=0.0030
POSITION_MIN_REWARD_RISK=1.15
POSITION_STRUCTURE_BUFFER_ATR=0.20
```

The `0.20%` normal limit prevents a routine loss from becoming many times larger than a normal winner. The `0.30%` emergency stop is catastrophic broker-side protection for gaps, process failure, or connectivity failure. It is not the target loss for every trade.

The strongest prior exit behavior, trailing profit protection, remains active. It is complemented by maximum favorable excursion, or MFE, giveback control:

```dotenv
POSITION_PROFIT_GIVEBACK_MIN_MFE_PCT=0.00080
POSITION_MAX_PROFIT_GIVEBACK_FRACTION=0.50
```

After a trade reaches the minimum MFE, the manager compares current profit with peak profit. If the trade gives back at least the configured fraction while remaining economically profitable, it requests a protected exit. Every stop replacement and exit request is recorded in `position_management_events` with MFE, maximum adverse excursion, spread, economic breakeven, playbook, and strategy path.

Session-close management is staged:

1. At 30 minutes before close, profitable positions receive a tighter profit lock.
2. At 15 minutes before close, weak, negative, or nonproductive positions are reduced or exited.
3. At the execution shutdown boundary, entries are already frozen and broker reconciliation drives the account toward zero exposure.
4. At 8 minutes before close, any remaining managed position receives a force-flatten request.
5. Shutdown verifies both zero GLD position and zero open GLD orders; a request without broker confirmation is not reported as success.

Relevant values:

```dotenv
POSITION_CLOSE_MANAGEMENT_MINUTES_BEFORE_CLOSE=30
POSITION_CLOSE_RISK_REDUCTION_MINUTES_BEFORE_CLOSE=15
POSITION_FORCE_FLATTEN_MINUTES_BEFORE_CLOSE=8
EXECUTION_ENTRY_FREEZE_MINUTES_BEFORE_CLOSE=15
EXECUTION_SESSION_FLATTEN_MINUTES_BEFORE_CLOSE=10
```

Entry and exit models are separate. `train-exit-model` uses only completed, reconciled, after-cost outcomes and position-management events. It refuses to train below `EXIT_MODEL_MIN_TRUSTWORTHY_OUTCOMES`, defaults to 500, and creates an advisory `exit:<strategy-path>:<playbook>` candidate. It does not share an entry target and is not automatically promoted into live exit control.

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\.venv\Scripts\python.exe -m gld_scalper.main train-exit-model `
  --strategy-path minute `
  --playbook proper_breakout
```

A `skipped` result is correct when the database does not yet have enough trustworthy exit examples.

### Phase 5: Risk And Position Sizing

Position size is derived from the maximum permitted loss and the actual setup stop:

```text
maximum dollar loss = account equity * MAX_TRADE_RISK_PCT
risk-sized notional = maximum dollar loss / stop-distance percentage
final notional = minimum of risk-sized notional and all quality/exposure caps
```

This means account buying power does not decide size by itself. A wider valid stop produces fewer shares. A one-share rounding decision is recorded through `maximum_loss`, `stop_distance`, `target_distance`, `risk_multiplier`, and `risk_details_json` on the order.

Unvalidated paper candidates remain small:

```dotenv
PAPER_EXPLORATION_MAX_NOTIONAL=1000
PAPER_LEARNING_EXPLORATION_MAX_NOTIONAL=2000
EXPERIMENTAL_PROFIT_FACTOR_THRESHOLD=1.15
EXPERIMENTAL_SIZE_MULTIPLIER=0.25
```

The bot can use a larger paper size only when all of these are true:

- after-cost validated profit factor meets the configured threshold;
- the model passed independent walk-forward validation;
- the model direction agrees with the deterministic setup;
- pattern quality is clean;
- spread is not wide;
- liquidity is good;
- data is fresh;
- no volatility burst is active.

Sizing is reduced for wide or unstable spread, poor liquidity, a volatility burst, stale data, weak confidence, correlated exposure, adverse macro context, event risk, order-block conflict, or adverse option intelligence. Macro and LLM context can increase size by no more than `LLM_CONTEXT_MAX_SIZING_ADJUSTMENT`, defaults to `5%`, and only after the independent execution and risk gates pass.

Paper mode keeps safety controls:

```dotenv
PAPER_MAX_SESSION_LOSS_PCT=0.005
PAPER_MAX_DRAWDOWN_PCT=0.0075
PAPER_MAX_CONSECUTIVE_LOSSES=6
PAPER_MAX_TRADES_PER_DAY=250
PAPER_MAX_ORDERS_PER_MINUTE=12
RISK_EXECUTION_ERROR_LIMIT=3
MAX_CORRELATED_EXPOSURE_PCT=0.10
```

These are deliberately looser than a future live profile, but they are not disabled. `PAPER_MAX_TRADES_PER_DAY` remains the normal-strategy session cap. A trade tagged as controlled exploration is exempt from that count only when `PAPER_LEARNING_MAX_EXPLORATION_TRADES_PER_DAY=0`; all other account, exposure, loss, drawdown, order-rate, data, and execution controls continue to apply. A learning system should learn market decisions, not repeated broker rejects, duplicate orders, stale feeds, or uncontrolled account drawdown.

### Phase 6: Scoped Supervised ML

The model family is no longer one universal classifier. The registry supports independent champions for:

```text
entry:fast_microstructure
entry:minute
entry:news_event
entry:playbook:proper_breakout
entry:playbook:false_break_reversal
entry:playbook:pullback_continuation
entry:playbook:compression_breakout
entry:playbook:trend_continuation
entry:playbook:spread_capture
exit:<strategy-path>:<playbook>
```

At inference time the predictor asks for the most specific scope. A playbook model can fall back to `entry:minute`, then `entry:all`. A fast microstructure decision can fall back only to `entry:all`; it does not silently use a minute-playbook model.

#### Actual Executed Action

`decision_executions` is the authoritative bridge between a strategy decision and training. It records:

- original action;
- actual executed action;
- blocked, submitted, partial, failed, or completed status;
- client order and root episode IDs;
- strategy path and playbook;
- selected model scope and version;
- spread and expected slippage;
- fill-quality score;
- short-direction availability;
- session phase;
- execution error;
- the execution-time feature snapshot.

The paper dataset joins this exact row. It does not infer a trade by looking for an order with a nearby timestamp. If a rule said `LONG` but the risk engine blocked it, the training action is `NO_TRADE`. If a short was unavailable, the model receives the direction-availability and execution-status evidence instead of being taught that a short was executed.

#### Labels And Costs

Each minute and fast decision receives independent cost-aware 1, 3, 5, and 15-minute labels. One-second midpoint snapshots are preferred; completed one-minute bars are the fallback. Each directional return is reduced by observed spread, estimated slippage, and available live execution costs before becoming `long_good`, `short_good`, or `no_trade`.

The trainer handles the large `NO_TRADE` class using:

- chronological balanced sampling;
- balanced model class weights where supported;
- separate chronological calibration data;
- confidence and probability-margin tuning;
- balanced accuracy and macro F1;
- expected calibration error;
- after-cost profit factor, expectancy, return, and drawdown;
- meaningful model abstention.

A low-confidence or low-margin output becomes `no_trade`. This is a model action, not an error.

#### Exact Candidate Evaluation

Every new format-v3 artifact contains:

- the fitted preprocessing and model pipeline;
- exact ordered feature columns;
- feature profile;
- estimator hyperparameters;
- optimized confidence and margin thresholds;
- training feature statistics;
- training-data start and end;
- model scope;
- fitted-model state fingerprint;
- complete artifact fingerprint;
- holdout metrics;
- walk-forward metrics;
- paper metrics;
- regime metrics.

The walk-forward evaluator receives the selected candidate's exact base estimator, hyperparameters, feature columns, and policy thresholds. Results are reported separately as `holdout_*`, `walk_forward_*`, `paper_*`, and `regime_metrics`. The predictor rejects a new artifact if the registered artifact fingerprint or fitted-model state fingerprint does not match.

#### Promotion, Preservation, Rollback, And Drift

Promotion defaults require:

```dotenv
PROMOTION_MIN_PROFIT_FACTOR=1.20
PROMOTION_MIN_WIN_RATE=0.48
PROMOTION_MAX_DRAWDOWN=0.015
PROMOTION_MIN_TRADE_COUNT=100
PROMOTION_MIN_PROFITABLE_FOLD_RATIO=0.60
PROMOTION_MAX_CALIBRATION_ERROR=0.15
PROMOTION_MIN_FOLD_COUNT=3
PROMOTION_REQUIRE_PAPER_RESULTS=true
PROMOTION_MIN_PAPER_TRADE_COUNT=30
PROMOTION_MIN_REGIME_COUNT=2
```

A candidate must pass absolute floors and beat the current champion in the same scope. Promotion never deletes the prior champion. It changes the prior row to `archived`, preserves its file, fingerprint, data range, metrics, and parent version, then writes `model_champion_history`.

Rollback is explicit:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main rollback-model `
  --scope "entry:minute" `
  --model-version "candidate-YYYYMMDD-HHMMSS-ffffff" `
  --reason "paper performance regression"
```

Drift checks each champion scope independently. Feature drift uses the champion's training statistics. Paper drift aggregates root trading episodes, not partial exit tranches, and compares after-cost return-based profit factor and drawdown with the model's validated range.

```dotenv
DRIFT_MIN_PREDICTIONS=100
DRIFT_MIN_PAPER_OUTCOMES=30
DRIFT_DEMOTION_SCORE=0.35
DRIFT_PROFIT_FACTOR_FLOOR_RATIO=0.70
DRIFT_DRAWDOWN_LIMIT_MULTIPLIER=1.50
```

When enough evidence crosses a configured limit, the champion is demoted to `archived`, live inference reloads its remaining fallback scopes, and the reason is saved in `model_drift_reports` and `model_champion_history`. Demotion does not delete the model and does not automatically promote a weaker replacement.

Run an operator report:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main ml-drift-report --limit 1000
```

#### Training Hours On An 8 GB Laptop

Scheduled fitting is restricted to the Eastern Time window below and also requires zero active execution episodes:

```dotenv
RETRAIN_ONLY_OUTSIDE_REGULAR_HOURS=true
RETRAIN_AFTER_HOUR_ET=20
RETRAIN_BEFORE_HOUR_ET=8
```

It does not fit while live orders are active. For large historical searches, stop `run-paper`, stop Ollama, and use the resumable `train-loop --continuous-historical` workflow described earlier. Training remains immutable: every candidate is a new file, and repeated experiment identities are skipped.

### Phase 7: Local FinGPT And Ollama Research

FinGPT and Ollama have different jobs:

- Local FinGPT source supplies financial sentiment concepts, forecasting prompt structure, RAG organization, and data-preparation patterns.
- `llama3.2:1b` performs lightweight local classification and review.
- Deterministic Python code decides whether news labels agree with later GLD behavior.
- The supervised models perform millisecond inference in the order path.
- The LLM never receives an execution client and never calls broker-order methods.

Configuration:

```dotenv
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434
LLM_MODEL=llama3.2:1b
LLM_TIMEOUT_SECONDS=240
ENABLE_LLM_ANALYSIS=true
ENABLE_LLM_MACRO_CONTEXT=true
ENABLE_LLM_REVIEW_COACH=true
ENABLE_LLM_TRAINING_ADVICE=true
ENABLE_LLM_TRAINING_LABELS=true
ENABLE_LLM_LIVE_TRADING=false
LLM_OFFLINE_ONLY=true
FINGPT_SOURCE_DIR=FINGPT/FinGPT-1.0.0/fingpt
LLM_CONTEXT_MAX_SIZING_ADJUSTMENT=0.05
LLM_NEWS_MIN_LINKED_FRACTION=0.60
```

The offline cycle fingerprints the available local FinGPT forecaster, RAG, and sentiment-template files. It then:

1. links news to subsequent GLD returns;
2. links news to subsequent spread behavior;
3. asks Ollama for financial sentiment, novelty, event type, event risk, and gold impact;
4. trusts direction only when price direction agrees, spread linkage exists, and classification confidence is at least 0.65;
5. creates hourly or daily GLD context covering gold, USD, Fed/rates, geopolitical risk, novelty, and event risk;
6. runs the local RAG coach over `Knowledge`, journals, outcomes, missed opportunities, and exports;
7. on daily runs, produces journal review, missed-opportunity analysis, advisory labels, and structured training advice;
8. writes an auditable `llm_offline_cycles` record.

Start Ollama in one terminal if it is not already listening:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
$env:OLLAMA_MODELS="D:\ALPACA TEST\gld_scalper_bot\_ollama_models"
.\OLLAMA\ollama.exe serve
```

Run an hourly research pass in another terminal after market hours:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main llm-offline-cycle --cadence hourly
```

Run the deeper daily pass:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main llm-offline-cycle --cadence daily
```

The command refuses regular market hours and refuses any open execution episode. `--force` only bypasses the clock check for deliberate maintenance; it does not permit an open episode and does not enable broker access.

Do not run full FinGPT 7B or 13B fine-tuning on the 8 GB laptop. Use a cloud GPU if that later becomes a research requirement. The local source integration does not claim that `llama3.2:1b` has been converted into or fine-tuned as FinGPT. It uses FinGPT's financial workflow patterns around a small local model.

### New Audit And Export Data

The SQLite migration preserves existing paper data and adds the following audit surfaces:

```text
decision_executions
model_champion_history
llm_offline_cycles
model_scope and fingerprint metadata in model_versions
paper-performance and demotion fields in model_drift_reports
price/spread linkage and trust fields in news_items
economic-breakeven, stop-distance, maximum-loss, playbook, and path fields in orders
```

Hourly CSV exports include these tables in their dedicated execution, ML, news, and LLM folders. Existing paper data remains under `data/paper`; a future live implementation must continue to use `data/live` and a separate registry.

### Recommended Paper-Session Sequence

Before the session:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\.venv\Scripts\python.exe -m gld_scalper.main init-db
.\.venv\Scripts\python.exe -m gld_scalper.main status
```

Verify Alpaca shows no unintended GLD position or open GLD order. Then run:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main run-paper
```

After the close, let broker-confirmed shutdown finish. Review root episodes separately from tranches, inspect after-cost P/L, and run the offline Ollama daily cycle only after live order processing has ended. Do not promote a model merely because classification accuracy improved; promotion must remain tied to after-cost holdout, walk-forward, paper, and regime evidence.

## Ubuntu Server Deployment

This is the common shape for an AWS EC2 Ubuntu deployment.

Create a server:

1. Launch Ubuntu 22.04 or 24.04.
2. Allow SSH only from your own trusted IP.
3. Do not expose public web ports for this bot.
4. Copy or clone the project to `/opt/gld_scalper_bot`.

Install system packages:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

Create the app folder:

```bash
sudo mkdir -p /opt/gld_scalper_bot
sudo chown -R "$USER":"$USER" /opt/gld_scalper_bot
cd /opt/gld_scalper_bot
```

Create Python environment:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"
```

Create `.env`:

```bash
nano .env
```

Use the same paper-only environment values shown above.

Initialize database:

```bash
./.venv/bin/python -m gld_scalper.main init-db
```

Run manually first:

```bash
./.venv/bin/python -m gld_scalper.main run-paper
```

Stop manual run with:

```bash
Ctrl+C
```

## Server Deployment With systemd

Copy the service file:

```bash
sudo cp systemd/gld-scalper.service /etc/systemd/system/gld-scalper.service
```

Edit paths/user if your server user is not `ubuntu`:

```bash
sudo nano /etc/systemd/system/gld-scalper.service
```

Reload systemd:

```bash
sudo systemctl daemon-reload
```

Start the service:

```bash
sudo systemctl start gld-scalper
```

Enable auto-start on reboot:

```bash
sudo systemctl enable gld-scalper
```

Watch service logs:

```bash
journalctl -u gld-scalper -f
```

Stop service:

```bash
sudo systemctl stop gld-scalper
```

Restart service:

```bash
sudo systemctl restart gld-scalper
```

Check service status:

```bash
sudo systemctl status gld-scalper
```

## Server File Locations

If deployed to `/opt/gld_scalper_bot`:

```text
/opt/gld_scalper_bot/.env
/opt/gld_scalper_bot/data/paper/gld_scalper.db
/opt/gld_scalper_bot/logs/bot.log
/opt/gld_scalper_bot/exports/paper/latest/trading_journal.csv
/opt/gld_scalper_bot/exports/paper/hourly/
/opt/gld_scalper_bot/models/
```

Back up these folders if you care about training history:

```text
data/
exports/
models/
logs/
```

## Production Hardening Ideas

Before moving from paper to live trading, do not simply change the endpoint. A live-trading conversion should be a separate engineering pass.

Recommended hardening:

- Add a dedicated live-trading approval switch.
- Add a broker kill-switch command.
- Add Slack, email, or SMS alerts for orders and errors.
- Move credentials to AWS Secrets Manager or another secret store.
- Add daily database backups.
- Add disk-space monitoring.
- Add separate order-update websocket handling through Alpaca `TradingStream`.
- Add stronger performance analysis before live use.
- Review all legal, tax, compliance, and brokerage obligations.

## Common Problems

`websocket connected but NO_TRADE`:

The stream can be connected while market data is stale outside market hours. That is expected.

`not enough labeled samples`:

The model cannot train yet. It needs enough signals and real trade outcomes.

`websocket disconnected`:

The risk engine blocks trading until the data stream is healthy again.

`account position reconciliation failed`:

The bot could not verify Alpaca account/position/order state. It blocks trading by design.

`unexpected open orders exist`:

Cancel or resolve open GLD paper orders in Alpaca, or wait for them to finish.

`execution circuit breaker open`:

The bot saw the configured number of consecutive broker, stream, reconciliation, or order-state failures. New entries remain frozen for that process. Read the preceding `execution_safety_events` rows and log messages, verify Alpaca position/order state, then restart only after the cause is understood.

`Startup broker reconciliation failed`:

The bot could not prove that Alpaca, SQLite, and protective-order state agree. It refuses to start the trading runtimes. Check internet access and Alpaca status, inspect the paper account for GLD orders or positions, and review `execution_safety_events`. Do not bypass the startup gate.

`paper bot shutdown could not confirm a flat GLD account`:

The cancellation or close request did not reach a broker-confirmed zero state before the timeout. Keep the terminal open, inspect Alpaca immediately, and manually close the paper position if necessary. The bot deliberately reports failure instead of claiming that an unconfirmed request succeeded.

`no champion model and rule score is not extreme`:

The rule setup was not strong enough for rule-only mode.

## Filtered EMA-Cross TradingView Strategy

The bot includes a Python translation of the TradingView Pine strategy
`backtest_ema_cross_v6_filtered.pine`. The implementation is named
`ema_cross_filtered`.

This route is enabled in the paper configuration. It is designed to collect
real paper evidence for the strategy without allowing an indicator to bypass
broker, market-data, execution, or account safety.

### What The Original Pine Strategy Does

The supplied Pine strategy has four main rules:

1. Calculate a fast exponential moving average with a default length of 10.
2. Calculate a slow exponential moving average with a default length of 20.
3. Produce a buy signal when EMA 10 crosses above EMA 20, or a sell signal when
   EMA 10 crosses below EMA 20.
4. Accept the cross only when ADX 14 is at least 20 and the 10-bar cooldown has
   completed.

The Pine strategy then uses:

- A stop at 1.5 times ATR 14.
- A target at 3.0 times ATR 14.
- A nominal reward-to-risk ratio of 2:1.
- A fixed TradingView test quantity of 100,000.
- A TradingView commission assumption of 0.05 percent.
- A TradingView slippage assumption of two ticks.

The EMA cross is a trend-transition signal. ADX attempts to reject crosses in
weak, non-trending markets. The cooldown reduces rapid re-entry after a prior
trade.

### Strengths And Limitations

Strengths:

- The entry rule is deterministic and easy to audit.
- Signals do not depend on discretionary interpretation.
- ADX removes some low-strength EMA crosses.
- ATR adapts the exit distance to current volatility.
- The 2:1 target-to-stop relationship can tolerate a win rate below 50 percent
  before costs, provided fills and losses follow the model.
- The same calculation can be evaluated on several timeframes.

Limitations:

- EMA crosses are lagging. A cross happens after part of the move has already
  occurred.
- ADX measures trend strength, not direction, and can remain high near trend
  exhaustion.
- Sideways markets can still produce repeated losing crosses.
- A result from TradingView is not automatically a realistic broker result.
  Fill timing, spread, partial fills, rejected orders, latency, and borrow
  availability matter.
- A 100,000-share quantity is not suitable for this bot.
- A higher-timeframe ATR stop can be too large for a scalp account's permitted
  loss.
- Signals on different timeframes can disagree.
- Historical performance does not establish future profitability.

The bot records these limitations instead of hiding them behind a BUY or SELL
label.

### Python Translation

The implementation is in:

```text
src/gld_scalper/ema_cross_strategy.py
```

It reproduces:

- Pine-style EMA crossover and crossunder rules.
- Wilder-style DMI/ADX smoothing for `ta.dmi(14, 14)`.
- Wilder ATR 14.
- ADX threshold filtering.
- A cooldown measured in completed bars for each timeframe.
- ATR-based stop and target geometry.

It evaluates these regular-session GLD timeframes by default:

```text
1 minute
5 minutes
15 minutes
30 minutes
45 minutes
60 minutes
```

Higher-timeframe bars are anchored to the New York Stock Exchange regular
session at 9:30 a.m. Eastern Time. For example, the first 60-minute bar is
9:30–10:30, not 9:00–10:00. The engine rejects incomplete timeframe bars. This
prevents future leakage and stops the same unfinished candle from changing an
already submitted signal.

### Signal Lifecycle

For every loop:

1. The bot loads enough one-minute GLD history to calculate every configured
   timeframe.
2. It constructs only completed, session-aligned bars.
3. It calculates EMA 10, EMA 20, ADX 14, and ATR 14 independently for each
   timeframe.
4. It detects a cross only when the previous completed bar was on the other
   side or equal.
5. It applies the ADX and cooldown filters.
6. It saves the raw cross, filter result, values, score, and reason in
   `ema_cross_signals`.
7. A database uniqueness rule prevents a restart or repeated minute loop from
   submitting the same timeframe/bar/direction signal twice.
8. Same-direction crosses arriving together are merged into one broker intent.
   Each contributing timeframe is still stored and labeled independently.
9. When simultaneous timeframes disagree, the highest timeframe is selected
   and the lower-timeframe conflicts are stored as `timeframe_conflict`.
10. The selected signal becomes the `ema_cross_filtered` playbook.
11. In paper-learning mode, paper signal authority converts the eligible cross
    into a small controlled probe.
12. The normal risk and execution pipeline decides whether that probe may reach
    Alpaca.

### Meaning Of "Take Every Signal"

Every eligible Pine-style cross is captured. It becomes a paper order only when
all non-negotiable controls permit it.

The EMA route cannot override:

- A disconnected or stale live stream.
- A stale quote or trade.
- A wide, zero, or unstable spread.
- Liquidity below the configured paper minimum.
- A closed market or the end-of-session entry freeze.
- A broker/SQLite reconciliation mismatch.
- Open unexpected orders.
- The order-rate, exposure, drawdown, or loss circuit breakers.
- GLD shortability checks.
- A conflicting active GLD direction.
- Failure to create a protected bracket order.

If one of these controls blocks the order, the cross remains in SQLite with the
exact block reason. That is useful learning data: the model can later compare
the hypothetical return with the reason the execution was prevented.

The bot does not submit both a long and a short GLD order at the same time.
Alpaca maintains net symbol exposure, so opposite signals must not create a
false mixed-direction episode.

### Position Size And Exits

The Pine script's fixed quantity of 100,000 is deliberately not copied into
paper execution. The bot uses the existing paper-learning notional cap,
stop-distance sizing, aggregate exposure limit, and account risk budget.

The initial exit geometry uses:

```text
stop distance   = 1.5 x signal-timeframe ATR 14
target distance = 3.0 x signal-timeframe ATR 14
target R        = 2.0
```

If the ATR stop is larger than the bot's maximum normal stop, it is capped at
the safety limit and the record is marked `normal_risk_cap`. The target remains
2R relative to the accepted stop.

After entry, the existing protected position manager remains active. It can
apply economic breakeven, trailing-profit, profit-giveback, emergency-stop, and
session-close rules. Those controls use broker-confirmed orders and do not wait
for the next EMA signal.

### Learning Records

The new SQLite table is:

```text
ema_cross_signals
```

It records:

- Decision and timeframe.
- Source bar timestamp and decision timestamp.
- Current and previous EMA relationship.
- ADX and ATR.
- ADX pass/fail.
- Cooldown pass/fail.
- Score and confidence.
- Eligibility.
- Execution status.
- Linked main signal ID.
- Linked client order ID.
- Block reason.
- Full feature snapshot.

EMA crosses receive cost-aware labels after 1, 3, 5, and 15 minutes. The labels
use one-second price snapshots when available and one-minute bars as the
fallback. They include spread, estimated slippage, fees, favorable excursion,
adverse excursion, and the direction that would have produced a useful
after-cost move.

Executed EMA trades also enter the normal:

```text
signals
decision_executions
orders
fills
trading_journal
trade_outcomes
trade_reviews
outcome_labels
```

This means scheduled training can learn from the actual executed action and
later return, while the dedicated EMA table preserves all raw strategy
evidence.

Hourly CSV exports include:

```text
exports/paper/latest/strategy/ema_cross_signals/ema_cross_signals.csv
exports/paper/hourly/<export timestamp>/strategy/ema_cross_signals/ema_cross_signals.csv
```

### Configuration

```dotenv
ENABLE_EMA_CROSS_STRATEGY=true
EMA_CROSS_TIMEFRAMES=1 5 15 30 45 60
EMA_CROSS_HISTORY_MINUTES=3900
EMA_CROSS_FAST_PERIOD=10
EMA_CROSS_SLOW_PERIOD=20
EMA_CROSS_USE_ADX_FILTER=true
EMA_CROSS_ADX_PERIOD=14
EMA_CROSS_ADX_THRESHOLD=20
EMA_CROSS_USE_COOLDOWN=true
EMA_CROSS_COOLDOWN_BARS=10
EMA_CROSS_ATR_PERIOD=14
EMA_CROSS_STOP_LOSS_ATR_MULTIPLE=1.5
EMA_CROSS_TAKE_PROFIT_ATR_MULTIPLE=3.0
EMA_CROSS_PAPER_SIGNAL_AUTHORITY=true
```

Keep `EMA_CROSS_PAPER_SIGNAL_AUTHORITY=true` only for paper-learning
experiments. It is bounded by `PAPER_LEARNING_EXPLORATION_MAX_NOTIONAL` and the
other paper risk controls.

### Inspect The Route

Check configuration and the paper account:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\.venv\Scripts\python.exe -m gld_scalper.main status
```

The output should show:

```text
paper_trading_enforced: true
ema_cross_strategy_enabled: true
ema_cross_paper_signal_authority: true
ema_cross_timeframes: [1, 5, 15, 30, 45, 60]
```

Watch EMA events while the bot runs:

```powershell
Get-Content .\logs\bot.log -Wait |
  Select-String "EMA cross|ema_cross|order_submit|no trade"
```

Start paper trading:

```powershell
cd "D:\ALPACA TEST\gld_scalper_bot"
.\.venv\Scripts\python.exe -m gld_scalper.main run-paper
```

Stop with `Ctrl+C`. Wait for the shutdown reconciliation and flat-account
confirmation before closing PowerShell.

## Final Readiness Standard

Before a paper-trading session, the following should pass:

```powershell
.\.venv\Scripts\ruff.exe check src tools tests --no-cache
.\.venv\Scripts\pytest.exe
.\.venv\Scripts\python.exe -m compileall src tests
.\.venv\Scripts\python.exe -m pip check
```

Only run `reset-data` here if you intentionally want to delete the active paper dataset. For normal paper-trading sessions, keep the data and start the bot:

```powershell
.\.venv\Scripts\python.exe -m gld_scalper.main run-paper
```
