package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** Converts gateway field names and values into bounded operator-facing text. */
final class HumanReadableFormatter {
    private static final DateTimeFormatter LOCAL_TIME = DateTimeFormatter.ofPattern("dd MMM yyyy, HH:mm:ss z")
            .withZone(ZoneId.systemDefault());
    private static final Set<String> SECRET_PARTS = Set.of("secret", "token", "password", "api_key", "authorization");
    private static final Set<String> MONEY_PARTS = Set.of("pnl", "equity", "cash", "buying_power", "price", "cost", "slippage", "fee");

    private HumanReadableFormatter() { }

    static String label(String key) {
        if (key == null || key.isBlank()) return "Value";
        String normalized = key.replace('_', ' ').replace('-', ' ').replace('.', ' ').replace(':', ' ').trim();
        StringBuilder result = new StringBuilder();
        for (String word : normalized.split("\\s+")) {
            if (!result.isEmpty()) result.append(' ');
            String lower = word.toLowerCase(Locale.ROOT);
            result.append(switch (lower) {
                case "id" -> "ID";
                case "api" -> "API";
                case "llm" -> "LLM";
                case "ml" -> "ML";
                case "pnl" -> "P/L";
                case "mfe" -> "MFE";
                case "mae" -> "MAE";
                case "usd" -> "USD";
                case "url" -> "URL";
                case "pid" -> "Process ID";
                default -> Character.toUpperCase(lower.charAt(0)) + lower.substring(1);
            });
        }
        return result.toString();
    }

    static String value(String key, JsonNode value) {
        if (value == null || value.isMissingNode() || value.isNull()) return "Not available";
        if (isSecret(key)) return value.asText("").isBlank() ? "Not configured" : "Configured securely";
        if (value.isBoolean()) return value.asBoolean() ? "Yes" : "No";
        if (value.isObject()) return value.size() == 0 ? "No details" : value.size() + " details available";
        if (value.isArray()) return value.size() == 0 ? "None" : value.size() + " items";

        String raw = value.asText();
        String lower = key == null ? "" : key.toLowerCase(Locale.ROOT);
        if (raw.isBlank()) return "Not available";
        if (looksLikeTimestamp(lower, raw)) {
            try { return LOCAL_TIME.format(Instant.parse(raw)); }
            catch (DateTimeParseException ignored) { }
        }
        if (value.isNumber()) {
            double number = value.asDouble();
            if (lower.contains("confidence") || lower.contains("probability") || lower.endsWith("_rate")
                    || lower.contains("drawdown") || lower.endsWith("_return") || lower.contains("accuracy")
                    || lower.contains("ece")) {
                return String.format(Locale.US, "%.2f%%", Math.abs(number) <= 1.0 ? number * 100.0 : number);
            }
            if (MONEY_PARTS.stream().anyMatch(lower::contains)) return String.format(Locale.US, "$%,.4f", number);
            if (lower.endsWith("_ms") || lower.contains("latency")) return String.format(Locale.US, "%,.2f ms", number);
            if (lower.endsWith("_seconds") || lower.endsWith("_age")) return String.format(Locale.US, "%,.1f seconds", number);
            if (Math.rint(number) == number) return String.format(Locale.US, "%,.0f", number);
            return String.format(Locale.US, "%,.4f", number);
        }
        if (lower.contains("status") || lower.contains("state") || lower.contains("decision") || lower.contains("regime")
                || lower.endsWith("_type") || lower.equals("action")) {
            return label(raw);
        }
        return raw.replace('_', ' ');
    }

    static List<Section> sections(JsonNode payload) {
        List<Section> result = new ArrayList<>();
        if (payload == null || payload.isMissingNode() || payload.isNull()) return result;
        if (!payload.isObject()) {
            result.add(new Section("Result", List.of(new Field("Value", value("value", payload)))));
            return result;
        }
        List<Field> overview = new ArrayList<>();
        payload.fields().forEachRemaining(entry -> {
            if (entry.getValue().isContainerNode()) {
                List<Field> fields = flatten(entry.getValue(), 14);
                if (!fields.isEmpty()) result.add(new Section(label(entry.getKey()), fields));
            } else if (!isSecret(entry.getKey())) {
                overview.add(new Field(label(entry.getKey()), value(entry.getKey(), entry.getValue())));
            }
        });
        if (!overview.isEmpty()) result.add(0, new Section("Overview", overview));
        return result;
    }

    static String plainText(JsonNode payload) {
        StringBuilder text = new StringBuilder();
        for (Section section : sections(payload)) {
            if (!text.isEmpty()) text.append('\n');
            text.append(section.title().toUpperCase(Locale.ROOT)).append('\n');
            for (Field field : section.fields()) text.append(field.label()).append(": ").append(field.value()).append('\n');
        }
        return text.isEmpty() ? "No information is available." : text.toString().trim();
    }

    private static List<Field> flatten(JsonNode node, int limit) {
        List<Field> fields = new ArrayList<>();
        if (node.isArray()) {
            int shown = Math.min(node.size(), limit);
            for (int index = 0; index < shown; index++) {
                JsonNode item = node.get(index);
                fields.add(new Field("Item " + (index + 1), item.isValueNode() ? value("item", item) : compactObject(item)));
            }
            if (node.size() > shown) fields.add(new Field("Additional items", (node.size() - shown) + " more available"));
            return fields;
        }
        node.fields().forEachRemaining(entry -> {
            if (fields.size() >= limit || isSecret(entry.getKey())) return;
            JsonNode child = entry.getValue();
            fields.add(new Field(label(entry.getKey()), child.isContainerNode() ? compactObject(child) : value(entry.getKey(), child)));
        });
        return fields;
    }

    private static String compactObject(JsonNode node) {
        if (node.isArray()) return node.size() + " items";
        List<String> parts = new ArrayList<>();
        node.fields().forEachRemaining(entry -> {
            if (parts.size() < 4 && !entry.getValue().isContainerNode() && !isSecret(entry.getKey())) {
                parts.add(label(entry.getKey()) + ": " + value(entry.getKey(), entry.getValue()));
            }
        });
        return parts.isEmpty() ? node.size() + " details available" : String.join(" | ", parts);
    }

    private static boolean looksLikeTimestamp(String key, String raw) {
        return key.contains("timestamp") || key.endsWith("_at") || (raw.contains("T") && raw.endsWith("Z"));
    }

    private static boolean isSecret(String key) {
        if (key == null) return false;
        String lower = key.toLowerCase(Locale.ROOT);
        return SECRET_PARTS.stream().anyMatch(lower::contains);
    }

    record Field(String label, String value) { }
    record Section(String title, List<Field> fields) { }
}
