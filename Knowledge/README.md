# Trading Knowledge Library

Human-authored strategy and research references used by developers and the offline RAG reviewer.

Return to the [project manual](../README.md).

## Folder Contract

- Keep runtime code deterministic and testable; Ollama and research jobs may not call broker-order methods.
- Never commit credentials, `.env`, SQLite databases, raw quotes/trades, CSV exports, or downloaded training data.
- Preserve paper, historical, and future live state separation.
- Add or update tests when behavior changes, and update this guide when file ownership changes.

## Files In This Folder

| File | Responsibility |
|---|---|
| [`15_Scalping_Strategies.pdf`](../Knowledge/15_Scalping_Strategies.pdf) | Version-controlled project resource. |
| [`171223848 0.pdf`](../Knowledge/171223848 0.pdf) | Version-controlled project resource. |
| [`a_guide_to_successful_gold_trading.pdf`](../Knowledge/a_guide_to_successful_gold_trading.pdf) | Version-controlled project resource. |
| [`Algorithmic Trading, Stochastic Control, and Mutually-Exciting Processes.pdf`](../Knowledge/Algorithmic Trading, Stochastic Control, and Mutually-Exciting Processes.pdf) | Version-controlled project resource. |
| [`Bob_volman_FOREX_PRICE_ACTION_SCALPING_a.pdf`](../Knowledge/Bob_volman_FOREX_PRICE_ACTION_SCALPING_a.pdf) | Version-controlled project resource. |
| [`ebook-forex-options-bank-en.pdf`](../Knowledge/ebook-forex-options-bank-en.pdf) | Version-controlled project resource. |
| [`Ebook_Trading_Profile_Scalping_EN.pdf`](../Knowledge/Ebook_Trading_Profile_Scalping_EN.pdf) | Version-controlled project resource. |
| [`FinGPT - Open-Source Financial Large Language Models.pdf`](../Knowledge/FinGPT - Open-Source Financial Large Language Models.pdf) | Version-controlled project resource. |
| [`Forex-Trading-For-Beginners-The-Ultimate-Guide.pdf`](../Knowledge/Forex-Trading-For-Beginners-The-Ultimate-Guide.pdf) | Version-controlled project resource. |
| [`Gold_or_BTC_The_Best_Trading_Strategy.pdf`](../Knowledge/Gold_or_BTC_The_Best_Trading_Strategy.pdf) | Version-controlled project resource. |
| [`High Frequency Trading - A Bibliography.pdf`](../Knowledge/High Frequency Trading - A Bibliography.pdf) | Version-controlled project resource. |
| [`High Frequency Trading for Gold and Silver Using the Hilbert Transform and Event Driven Volatility Modelling.pdf`](../Knowledge/High Frequency Trading for Gold and Silver Using the Hilbert Transform and Event Driven Volatility Modelling.pdf) | Version-controlled project resource. |
| [`high frequency trading.pdf`](../Knowledge/high frequency trading.pdf) | Version-controlled project resource. |
| [`High-frequency market-making for multi-dimensional Markov processes.pdf`](../Knowledge/High-frequency market-making for multi-dimensional Markov processes.pdf) | Version-controlled project resource. |
| [`Idenitfying-Chart-Patterns.pdf`](../Knowledge/Idenitfying-Chart-Patterns.pdf) | Version-controlled project resource. |
| [`Introduction to HFT scalping strategies.pdf`](../Knowledge/Introduction to HFT scalping strategies.pdf) | Version-controlled project resource. |
| [`keys-to-trading-gold-ca.pdf`](../Knowledge/keys-to-trading-gold-ca.pdf) | Version-controlled project resource. |
| [`L-0000572311-pdf.pdf`](../Knowledge/L-0000572311-pdf.pdf) | Version-controlled project resource. |
| [`Mastering-Gold-XAUUSD-Trading-with-PVT-Technique_(1).pdf`](../Knowledge/Mastering-Gold-XAUUSD-Trading-with-PVT-Technique_(1).pdf) | Version-controlled project resource. |
| [`My Learnings - High probability trading strategies.pdf`](../Knowledge/My Learnings - High probability trading strategies.pdf) | Version-controlled project resource. |
| [`NIPS-2017-attention-is-all-you-need-Paper.pdf`](../Knowledge/NIPS-2017-attention-is-all-you-need-Paper.pdf) | Version-controlled project resource. |
| [`QuantAgent - Price-Driven Multi-Agent LLMs for High-Frequency Trading.pdf`](../Knowledge/QuantAgent - Price-Driven Multi-Agent LLMs for High-Frequency Trading.pdf) | Version-controlled project resource. |
| [`The State of the Art of Large Language Models on Chartered Financial Analyst Exams.pdf`](../Knowledge/The State of the Art of Large Language Models on Chartered Financial Analyst Exams.pdf) | Version-controlled project resource. |
| [`the-candlestick-trading-bible-(KohanFx.com).pdf`](../Knowledge/the-candlestick-trading-bible-(KohanFx.com).pdf) | Version-controlled project resource. |
| [`Understanding_Price_Action_Bob_Volman.pdf`](../Knowledge/Understanding_Price_Action_Bob_Volman.pdf) | Version-controlled project resource. |
| [`vdoc.pub_the-forex-options-course-a-self-study-guide-to-trading-currency-options.pdf`](../Knowledge/vdoc.pub_the-forex-options-course-a-self-study-guide-to-trading-currency-options.pdf) | Version-controlled project resource. |

## Linkage And Change Discipline

1. Start at the composition root in `src/gld_scalper/main.py` or the invoking tool/script.
2. Follow typed settings from `config.py`; environment values should not be read ad hoc elsewhere.
3. Follow persistence through `database.py` and `schema.sql`; multi-row execution state must remain transactional.
4. Follow behavioral evidence into the matching tests before changing a public interface.
5. Run focused tests first, then the complete suite. Paper execution is the final verification stage, not the first.

## Data And Security

Tracked code and promoted model memory may be committed. Raw market data, account data, exports, logs, API keys, and local Ollama model blobs stay outside Git. Model artifacts must retain their checksum, manifest, training range, exact feature profile, metrics, and rollback lineage.

---

Copyright (c) Mashcorp. GLD Scalper Bot is a Mashcorp project.
