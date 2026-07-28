from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "architecture"
WIDTH = 2400
HEIGHT = 1800

INK = "#17202A"
MUTED = "#52606D"
LINE = "#637381"
BLUE = "#DDEEFF"
GREEN = "#DFF4E4"
AMBER = "#FFF0CC"
RED = "#FDE2E2"
VIOLET = "#EAE4FA"
TEAL = "#DDF3F1"
GRAY = "#EDF1F4"
WHITE = "#FFFFFF"


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ["seguisb.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    for name in names:
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


TITLE = font(54, bold=True)
SUBTITLE = font(26)
BOX_TITLE = font(28, bold=True)
BOX_BODY = font(22)
SMALL = font(19)
FOOTER = font(18)


def text_lines(text: str, max_width: int, draw: ImageDraw.ImageDraw, text_font) -> list[str]:
    output: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            output.append("")
            continue
        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            if draw.textbbox((0, 0), candidate, font=text_font)[2] <= max_width:
                line = candidate
            else:
                output.append(line)
                line = word
        output.append(line)
    return output


def box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    title: str,
    body: str,
    fill: str,
    *,
    border: str = LINE,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=18, fill=fill, outline=border, width=3)
    title_lines = text_lines(title, x2 - x1 - 36, draw, BOX_TITLE)
    body_lines = text_lines(body, x2 - x1 - 36, draw, BOX_BODY)
    y = y1 + 18
    for line in title_lines:
        draw.text((x1 + 18, y), line, fill=INK, font=BOX_TITLE)
        y += 34
    y += 5
    for line in body_lines:
        draw.text((x1 + 18, y), line, fill=MUTED, font=BOX_BODY)
        y += 29
    return xy


def diamond(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    size: tuple[int, int],
    text: str,
    fill: str = AMBER,
) -> tuple[int, int, int, int]:
    cx, cy = center
    w, h = size
    points = [(cx, cy - h // 2), (cx + w // 2, cy), (cx, cy + h // 2), (cx - w // 2, cy)]
    draw.polygon(points, fill=fill, outline=LINE, width=3)
    lines = text_lines(text, int(w * 0.62), draw, BOX_TITLE)
    total = len(lines) * 34
    y = cy - total // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=BOX_TITLE)
        draw.text((cx - (bbox[2] - bbox[0]) / 2, y), line, fill=INK, font=BOX_TITLE)
        y += 34
    return (cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2)


def center(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


def arrow(
    draw: ImageDraw.ImageDraw,
    source: tuple[int, int, int, int],
    target: tuple[int, int, int, int],
    *,
    label: str | None = None,
    color: str = LINE,
) -> None:
    sx, sy = center(source)
    tx, ty = center(target)
    if abs(ty - sy) >= abs(tx - sx):
        start = (sx, source[3] if ty > sy else source[1])
        end = (tx, target[1] if ty > sy else target[3])
        middle = (start[1] + end[1]) // 2
        points = [start, (start[0], middle), (end[0], middle), end]
    else:
        start = (source[2] if tx > sx else source[0], sy)
        end = (target[0] if tx > sx else target[2], ty)
        middle = (start[0] + end[0]) // 2
        points = [start, (middle, start[1]), (middle, end[1]), end]
    draw.line(points, fill=color, width=5, joint="curve")
    previous = points[-2]
    vx, vy = end[0] - previous[0], end[1] - previous[1]
    mag = max((vx * vx + vy * vy) ** 0.5, 1)
    ux, uy = vx / mag, vy / mag
    px, py = -uy, ux
    tip = end
    base = (end[0] - ux * 22, end[1] - uy * 22)
    draw.polygon(
        [
            tip,
            (base[0] + px * 10, base[1] + py * 10),
            (base[0] - px * 10, base[1] - py * 10),
        ],
        fill=color,
    )
    if label:
        segments = list(zip(points, points[1:]))
        label_start, label_end = max(
            segments,
            key=lambda pair: abs(pair[1][0] - pair[0][0]) + abs(pair[1][1] - pair[0][1]),
        )
        mx, my = (label_start[0] + label_end[0]) // 2, (label_start[1] + label_end[1]) // 2
        bbox = draw.textbbox((0, 0), label, font=SMALL)
        pad = 7
        draw.rounded_rectangle(
            (mx - (bbox[2] - bbox[0]) // 2 - pad, my - 18, mx + (bbox[2] - bbox[0]) // 2 + pad, my + 14),
            radius=5,
            fill=WHITE,
        )
        draw.text((mx - (bbox[2] - bbox[0]) / 2, my - 15), label, fill=MUTED, font=SMALL)


def canvas(title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    draw.text((90, 55), title, fill=INK, font=TITLE)
    draw.text((92, 122), subtitle, fill=MUTED, font=SUBTITLE)
    draw.line((90, 170, WIDTH - 90, 170), fill="#CBD2D9", width=3)
    return image, draw


def save(image: Image.Image, name: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    draw = ImageDraw.Draw(image)
    footer = "GLD Scalper Bot | Architecture reference | Copyright (c) Mashcorp"
    draw.text((90, HEIGHT - 42), footer, fill=MUTED, font=FOOTER)
    image.save(OUTPUT / name, format="JPEG", quality=95, subsampling=0, optimize=True)


def runtime_architecture() -> None:
    image, draw = canvas(
        "GLD Scalper Bot: Complete Runtime Architecture",
        "Live data, deterministic strategy, machine learning, execution safety, learning, and Ollama boundaries",
    )
    feed1 = box(draw, (90, 220, 710, 355), "Alpaca live stream", "Quotes, trades, bars and corrections", BLUE)
    feed2 = box(draw, (890, 220, 1510, 355), "Historical and research APIs", "Bars, quotes, trades, news, calendars and FRED", BLUE)
    feed3 = box(draw, (1690, 220, 2310, 355), "Local research knowledge", "Knowledge documents, CSV exports, journals and reports", VIOLET)

    ingest = box(draw, (250, 425, 1050, 570), "Data ingestion and diagnostics", "Timestamp validation, message age, quote/trade/bar freshness, gaps and disconnects", TEAL)
    database = box(draw, (1350, 425, 2150, 570), "Separated SQLite memory", "Paper, historical and future live databases; raw data, decisions, orders, outcomes and models", GRAY)
    for feed in (feed1, feed2):
        arrow(draw, feed, ingest)
    arrow(draw, feed3, database)
    arrow(draw, ingest, database)

    technical = box(draw, (90, 650, 620, 835), "Technical features", "EMA, SMA, RSI, ATR, VWAP, MACD, ADX, Bollinger, OBV, MFI and SAR", GREEN)
    pattern = box(draw, (680, 650, 1210, 835), "Price action and structure", "Buildup, compression, proper/false break, pullback, levels, order blocks, Fibonacci and FVG", GREEN)
    micro = box(draw, (1270, 650, 1800, 835), "Microstructure", "Spread, imbalance, trade intensity, signed pressure, volatility burst and liquidity", GREEN)
    context = box(draw, (1860, 650, 2310, 835), "Gold context", "Session phase, related assets, macro, news, event and options intelligence", GREEN)
    for feature in (technical, pattern, micro, context):
        arrow(draw, database, feature)

    agents = box(draw, (90, 930, 720, 1105), "Deterministic agents and playbooks", "IndicatorAgent, PatternAgent, TrendAgent and RiskAgent evaluate separate setup rules", AMBER)
    models = box(draw, (885, 930, 1515, 1105), "Approved model services", "Classical ML fallback plus asynchronous causal Transformer predictions and abstention", AMBER)
    ollama = box(draw, (1680, 930, 2310, 1105), "Ollama research coach", "Offline RAG, journal review, news labels and training advice. No broker access.", VIOLET)
    arrow(draw, technical, agents)
    arrow(draw, pattern, agents)
    arrow(draw, micro, models)
    arrow(draw, context, models)
    arrow(draw, database, ollama)

    fusion = box(draw, (250, 1190, 1050, 1355), "Decision fusion and target exposure", "Combine rule evidence, playbook confirmation, calibrated model advice and meaningful NO_TRADE abstention", TEAL)
    risk = box(draw, (1350, 1190, 2150, 1355), "Hard safety and risk authority", "Freshness, spread, liquidity, reconciliation, circuit breaker, close window, quantity, stop and target", RED)
    arrow(draw, agents, fusion)
    arrow(draw, models, fusion)
    arrow(draw, ollama, fusion)
    arrow(draw, fusion, risk)

    execution = box(draw, (90, 1450, 760, 1645), "Order-intent coordinator", "Idempotent serialized entry, cancel, replace, direction switch and close operations", BLUE)
    broker = box(draw, (865, 1450, 1535, 1645), "Alpaca paper broker", "Bracket parent/children, acknowledgements, partial fills, rejects and position state", BLUE)
    learning = box(draw, (1640, 1450, 2310, 1645), "Journal, labels and promotion", "Root episode P/L, MFE/MAE, 1/3/5/15m labels, holdout, walk-forward and champion registry", GREEN)
    arrow(draw, risk, execution)
    arrow(draw, execution, broker)
    arrow(draw, broker, learning)
    arrow(draw, learning, database)
    # Repaint nodes after connectors so long feedback lines never obscure text.
    box(draw, feed1, "Alpaca live stream", "Quotes, trades, bars and corrections", BLUE)
    box(draw, feed2, "Historical and research APIs", "Bars, quotes, trades, news, calendars and FRED", BLUE)
    box(draw, feed3, "Local research knowledge", "Knowledge documents, CSV exports, journals and reports", VIOLET)
    box(draw, ingest, "Data ingestion and diagnostics", "Timestamp validation, message age, quote/trade/bar freshness, gaps and disconnects", TEAL)
    box(draw, database, "Separated SQLite memory", "Paper, historical and future live databases; raw data, decisions, orders, outcomes and models", GRAY)
    box(draw, technical, "Technical features", "EMA, SMA, RSI, ATR, VWAP, MACD, ADX, Bollinger, OBV, MFI and SAR", GREEN)
    box(draw, pattern, "Price action and structure", "Buildup, compression, proper/false break, pullback, levels, order blocks, Fibonacci and FVG", GREEN)
    box(draw, micro, "Microstructure", "Spread, imbalance, trade intensity, signed pressure, volatility burst and liquidity", GREEN)
    box(draw, context, "Gold context", "Session phase, related assets, macro, news, event and options intelligence", GREEN)
    box(draw, agents, "Deterministic agents and playbooks", "IndicatorAgent, PatternAgent, TrendAgent and RiskAgent evaluate separate setup rules", AMBER)
    box(draw, models, "Approved model services", "Classical ML fallback plus asynchronous causal Transformer predictions and abstention", AMBER)
    box(draw, ollama, "Ollama research coach", "Offline RAG, journal review, news labels and training advice. No broker access.", VIOLET)
    box(draw, fusion, "Decision fusion and target exposure", "Combine rule evidence, playbook confirmation, calibrated model advice and meaningful NO_TRADE abstention", TEAL)
    box(draw, risk, "Hard safety and risk authority", "Freshness, spread, liquidity, reconciliation, circuit breaker, close window, quantity, stop and target", RED)
    box(draw, execution, "Order-intent coordinator", "Idempotent serialized entry, cancel, replace, direction switch and close operations", BLUE)
    box(draw, broker, "Alpaca paper broker", "Bracket parent/children, acknowledgements, partial fills, rejects and position state", BLUE)
    box(draw, learning, "Journal, labels and promotion", "Root episode P/L, MFE/MAE, 1/3/5/15m labels, holdout, walk-forward and champion registry", GREEN)
    save(image, "runtime-architecture.jpg")


def decision_flow() -> None:
    image, draw = canvas(
        "GLD Scalper Bot: Decision And Order Flow",
        "Every entry route converges on the same safety, reconciliation, sizing, and episode-accounting controls",
    )
    event = box(draw, (760, 215, 1640, 340), "New market event", "Quote, trade, one-second snapshot or completed bar", BLUE)
    fresh = diamond(draw, (1200, 465), (760, 180), "Required live data fresh?")
    features = box(draw, (730, 585, 1670, 735), "Build causal feature snapshot", "Indicators, patterns, microstructure, session, macro and model-ready values", GREEN)
    setup = diamond(draw, (1200, 865), (860, 190), "Playbook and model thresholds pass?")
    exploration = diamond(draw, (500, 1070), (700, 190), "Controlled paper exploration eligible?")
    risk = diamond(draw, (1500, 1070), (760, 190), "Hard risk and broker-safety gates pass?")
    reject = box(draw, (90, 1325, 690, 1485), "NO_TRADE / blocked", "Save the exact reason, features, model probabilities and future outcome labels", RED)
    geometry = box(draw, (910, 1325, 1490, 1485), "Build protected order geometry", "Risk-sized quantity, limit, economic breakeven, stop and target", AMBER)
    coordinate = box(draw, (1710, 1325, 2310, 1485), "Execute and reconcile", "Atomic bracket, second-check safety, fills, exits and broker/database consistency", BLUE)
    outcome = box(draw, (910, 1570, 1490, 1720), "Close root episode", "Record close reason, after-cost P/L, MFE/MAE, playbook and strategy path", GREEN)

    arrow(draw, event, fresh)
    arrow(draw, fresh, features)
    arrow(draw, fresh, reject)
    arrow(draw, features, setup)
    arrow(draw, setup, risk)
    arrow(draw, setup, exploration)
    arrow(draw, exploration, risk)
    arrow(draw, exploration, reject)
    arrow(draw, risk, geometry)
    arrow(draw, risk, reject)
    arrow(draw, geometry, coordinate)
    arrow(draw, coordinate, outcome)
    box(draw, event, "New market event", "Quote, trade, one-second snapshot or completed bar", BLUE)
    diamond(draw, center(fresh), (760, 180), "Required live data fresh?")
    box(draw, features, "Build causal feature snapshot", "Indicators, patterns, microstructure, session, macro and model-ready values", GREEN)
    diamond(draw, center(setup), (860, 190), "Playbook and model thresholds pass?")
    diamond(draw, center(exploration), (700, 190), "Controlled paper exploration eligible?")
    diamond(draw, center(risk), (760, 190), "Hard risk and broker-safety gates pass?")
    box(draw, reject, "NO_TRADE / blocked", "Save the exact reason, features, model probabilities and future outcome labels", RED)
    box(draw, geometry, "Build protected order geometry", "Risk-sized quantity, limit, economic breakeven, stop and target", AMBER)
    box(draw, coordinate, "Execute and reconcile", "Atomic bracket, second-check safety, fills, exits and broker/database consistency", BLUE)
    box(draw, outcome, "Close root episode", "Record close reason, after-cost P/L, MFE/MAE, playbook and strategy path", GREEN)
    save(image, "decision-and-order-flow.jpg")


def learning_flow() -> None:
    image, draw = canvas(
        "GLD Scalper Bot: Training, Validation And Promotion",
        "Training changes candidate weights; validation and paper evidence determine whether those weights gain authority",
    )
    raw = box(draw, (90, 230, 650, 390), "Raw trusted data", "Historical archive plus clean completed paper episodes", BLUE)
    prepare = box(draw, (730, 230, 1290, 390), "Pre-training preparation", "Causal features, cost-aware 1/3/5/15m labels, quality checks and fingerprint", TEAL)
    split = box(draw, (1370, 230, 1930, 390), "Chronological partitions", "Training, purged calibration, untouched holdout and walk-forward folds", AMBER)
    candidate = box(draw, (90, 560, 650, 735), "Candidate training", "Logistic, Naive Bayes, random forest, gradient boosting or scoped Transformer", GREEN)
    calibrate = box(draw, (730, 560, 1290, 735), "Calibration and abstention", "Class weighting, probability calibration, confidence and margin thresholds", GREEN)
    exact = box(draw, (1370, 560, 1930, 735), "Exact-artifact evaluation", "Saved features, preprocessing, hyperparameters, thresholds, costs and latency", GREEN)
    promotion = diamond(draw, (1200, 960), (900, 220), "Beats baseline and champion across required regimes?")
    reject = box(draw, (90, 1115, 690, 1285), "Reject but preserve", "Store metrics, fingerprint and rejection reason; candidate receives no authority", RED)
    shadow = box(draw, (900, 1115, 1500, 1285), "Shadow paper deployment", "Record predictions without originating orders; compare with real outcomes", VIOLET)
    champion = box(draw, (1710, 1115, 2310, 1285), "Bounded paper champion", "Authority only after sufficient profitable, calibrated and stable paper evidence", AMBER)
    monitor = box(draw, (900, 1435, 1500, 1605), "Drift and rollback", "Demote outside validated range; restore a preserved prior champion", RED)
    ollama = box(draw, (90, 1435, 690, 1605), "Ollama / FinGPT-inspired research", "Offline reviews, news context and advisory labels; never direct promotion", VIOLET)
    registry = box(draw, (1710, 1435, 2310, 1605), "Versioned model registry", "Artifacts, lineage, data range, metrics, champion history and rollback", GRAY)

    arrow(draw, raw, prepare)
    arrow(draw, prepare, split)
    arrow(draw, split, candidate)
    arrow(draw, candidate, calibrate)
    arrow(draw, calibrate, exact)
    arrow(draw, exact, promotion)
    arrow(draw, promotion, reject)
    arrow(draw, promotion, shadow)
    arrow(draw, shadow, champion)
    arrow(draw, champion, monitor)
    arrow(draw, monitor, registry)
    arrow(draw, champion, registry)
    arrow(draw, ollama, prepare)
    box(draw, raw, "Raw trusted data", "Historical archive plus clean completed paper episodes", BLUE)
    box(draw, prepare, "Pre-training preparation", "Causal features, cost-aware 1/3/5/15m labels, quality checks and fingerprint", TEAL)
    box(draw, split, "Chronological partitions", "Training, purged calibration, untouched holdout and walk-forward folds", AMBER)
    box(draw, candidate, "Candidate training", "Logistic, Naive Bayes, random forest, gradient boosting or scoped Transformer", GREEN)
    box(draw, calibrate, "Calibration and abstention", "Class weighting, probability calibration, confidence and margin thresholds", GREEN)
    box(draw, exact, "Exact-artifact evaluation", "Saved features, preprocessing, hyperparameters, thresholds, costs and latency", GREEN)
    diamond(draw, center(promotion), (900, 220), "Beats baseline and champion across required regimes?")
    box(draw, reject, "Reject but preserve", "Store metrics, fingerprint and rejection reason; candidate receives no authority", RED)
    box(draw, shadow, "Shadow paper deployment", "Record predictions without originating orders; compare with real outcomes", VIOLET)
    box(draw, champion, "Bounded paper champion", "Authority only after sufficient profitable, calibrated and stable paper evidence", AMBER)
    box(draw, monitor, "Drift and rollback", "Demote outside validated range; restore a preserved prior champion", RED)
    box(draw, ollama, "Ollama / FinGPT-inspired research", "Offline reviews, news context and advisory labels; never direct promotion", VIOLET)
    box(draw, registry, "Versioned model registry", "Artifacts, lineage, data range, metrics, champion history and rollback", GRAY)
    save(image, "training-validation-promotion.jpg")


def main() -> None:
    runtime_architecture()
    decision_flow()
    learning_flow()
    print(f"Rendered architecture diagrams to {OUTPUT}")


if __name__ == "__main__":
    main()
