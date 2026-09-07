package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import javafx.scene.control.Label;
import javafx.scene.control.ScrollPane;
import javafx.scene.control.TextArea;
import javafx.scene.control.TitledPane;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

/** Reusable operator view with a collapsed technical-payload escape hatch. */
final class HumanReadableView extends VBox {
    private final VBox content = new VBox(12);
    private final TextArea technical = new TextArea();
    private final TitledPane technicalPane = new TitledPane("Developer payload", technical);

    HumanReadableView() {
        super(8);
        getStyleClass().add("human-readable-view");
        technical.setEditable(false);
        technical.setWrapText(false);
        technical.setPrefRowCount(8);
        technicalPane.setExpanded(false);
        ScrollPane scroll = new ScrollPane(content);
        scroll.setFitToWidth(true);
        scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        scroll.getStyleClass().add("human-readable-scroll");
        getChildren().addAll(scroll, technicalPane);
        VBox.setVgrow(scroll, Priority.ALWAYS);
    }

    void show(JsonNode payload) {
        content.getChildren().clear();
        for (HumanReadableFormatter.Section section : HumanReadableFormatter.sections(payload)) {
            Label heading = new Label(section.title().toUpperCase());
            heading.getStyleClass().add("evidence-section-title");
            VBox block = new VBox(8, heading);
            block.getStyleClass().add("evidence-section");
            for (HumanReadableFormatter.Field field : section.fields()) {
                Label name = new Label(field.label());
                name.getStyleClass().add("evidence-field-name");
                Label value = new Label(field.value());
                value.setWrapText(true);
                value.getStyleClass().add("evidence-field-value");
                block.getChildren().add(new VBox(2, name, value));
            }
            content.getChildren().add(block);
        }
        if (content.getChildren().isEmpty()) content.getChildren().add(new Label("No information is available yet."));
        technical.setText(payload == null ? "" : payload.toPrettyString());
    }

    void showCombined(String title, JsonNode... payloads) {
        var mapper = new com.fasterxml.jackson.databind.ObjectMapper();
        var combined = mapper.createObjectNode();
        for (int index = 0; index < payloads.length; index++) combined.set(index == 0 ? title : title + " " + (index + 1), payloads[index]);
        show(combined);
    }
}
