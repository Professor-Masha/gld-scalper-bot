package com.mashcorp.jarvis;

import com.fasterxml.jackson.databind.JsonNode;
import javafx.geometry.Insets;
import javafx.scene.control.Label;
import javafx.scene.control.ProgressIndicator;
import javafx.scene.control.ScrollPane;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

/** Human-readable, read-only view of one lazily loaded memory node. */
final class NodeInspector extends VBox {
    private final Label title = new Label("SELECT A MEMORY NODE");
    private final Label subtitle = new Label("Choose a node to inspect the evidence the bot remembers.");
    private final VBox sections = new VBox(12);
    private final ProgressIndicator progress = new ProgressIndicator();

    NodeInspector() {
        super(8);
        getStyleClass().add("memory-inspector");
        setPadding(new Insets(12)); setMinWidth(250); setPrefWidth(370);
        title.getStyleClass().add("memory-node-title");
        subtitle.getStyleClass().add("memory-node-type"); subtitle.setWrapText(true);
        progress.setMaxSize(28, 28); progress.setVisible(false); progress.setManaged(false);
        ScrollPane scroll = new ScrollPane(sections); scroll.setFitToWidth(true); scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        scroll.getStyleClass().add("memory-inspector-scroll");
        getChildren().addAll(title, subtitle, progress, scroll); VBox.setVgrow(scroll, Priority.ALWAYS);
    }

    void loading(String label) {
        title.setText(label == null || label.isBlank() ? "LOADING MEMORY" : label.toUpperCase());
        subtitle.setText("Retrieving a compact, read-only evidence view...");
        progress.setVisible(true); progress.setManaged(true); sections.getChildren().clear();
    }

    void preview(GraphRenderPlan.NodePlan node) {
        sections.getChildren().clear();
        title.setText(node.label().toUpperCase());
        String type = node.type().replace('_', ' ').toUpperCase();
        String status = node.status().replace('_', ' ').toUpperCase();
        subtitle.setText(type + "  //  " + status);
        Label heading = new Label("AVAILABLE NOW");
        heading.getStyleClass().add("inspector-section-title");
        VBox fields = new VBox(8, heading);
        addPreviewField(fields, "Context", node.subtitle());
        addPreviewField(fields, "Summary", node.preview());
        sections.getChildren().add(fields);
        progress.setVisible(true);
        progress.setManaged(true);
    }

    void show(JsonNode detail) {
        progress.setVisible(false); progress.setManaged(false); sections.getChildren().clear();
        title.setText(detail.path("title").asText("MEMORY NODE").toUpperCase());
        subtitle.setText(detail.path("type").asText("memory").replace('_', ' ').toUpperCase() + "  //  " + detail.path("summary").asText("Read-only evidence"));
        detail.path("sections").forEach(section -> {
            Label heading = new Label(section.path("title").asText("Evidence").toUpperCase()); heading.getStyleClass().add("inspector-section-title");
            VBox fields = new VBox(8, heading);
            section.path("fields").forEach(field -> {
                Label name = new Label(field.path("label").asText("Field")); name.getStyleClass().add("inspector-field-name");
                Label value = new Label(field.path("value").asText("--")); value.setWrapText(true); value.getStyleClass().add("inspector-field-value");
                fields.getChildren().add(new VBox(2, name, value));
            });
            sections.getChildren().add(fields);
        });
        if (sections.getChildren().isEmpty()) sections.getChildren().add(new Label("No additional persisted evidence is available."));
    }

    private static void addPreviewField(VBox target, String nameText, String valueText) {
        if (valueText == null || valueText.isBlank()) return;
        Label name = new Label(nameText);
        name.getStyleClass().add("inspector-field-name");
        Label value = new Label(valueText);
        value.setWrapText(true);
        value.getStyleClass().add("inspector-field-value");
        target.getChildren().add(new VBox(2, name, value));
    }
}
