package com.mashcorp.jarvis;

import javafx.animation.Animation;
import javafx.animation.AnimationTimer;
import javafx.animation.FadeTransition;
import javafx.animation.RotateTransition;
import javafx.animation.ScaleTransition;
import javafx.geometry.Point3D;
import javafx.scene.AmbientLight;
import javafx.scene.Group;
import javafx.scene.PerspectiveCamera;
import javafx.scene.PointLight;
import javafx.scene.SubScene;
import javafx.scene.paint.Color;
import javafx.scene.paint.PhongMaterial;
import javafx.scene.shape.Sphere;
import javafx.scene.shape.Cylinder;
import javafx.scene.shape.DrawMode;
import javafx.scene.control.Tooltip;
import javafx.scene.transform.Rotate;
import javafx.util.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.Random;
import java.util.function.Consumer;

/** Lightweight native 3D decision core. It visualizes state; it never computes orders. */
public final class DecisionCore3D {
    private final Group orbitA = orbit(148, 42, Color.web("#5cf2b5"));
    private final Group orbitB = orbit(112, 32, Color.web("#2de1ff"));
    private final Group orbitC = orbit(78, 24, Color.web("#ffbf69"));
    private final Sphere nucleus = new Sphere(43, 48);
    private final Sphere innerCore = new Sphere(22, 40);
    private final Sphere halo = new Sphere(57, 32);
    private final Group graphLayer = new Group();
    private final Group world;
    private final SubScene scene;
    private final RotateTransition spinA;
    private final RotateTransition spinB;
    private final RotateTransition spinC;
    private final ScaleTransition haloPulse;
    private final FadeTransition haloBreathe;
    private double dragX;
    private double dragY;
    private Consumer<String> selectionListener = node -> {};
    private final Map<String, Sphere> renderedNodes = new HashMap<>();
    private final Map<String, Cylinder> renderedEdges = new HashMap<>();
    private final Map<String, Boolean> activeEdges = new HashMap<>();
    private String fingerprint = "";
    private String selectedId;
    private boolean reducedMotion;
    private final AnimationTimer edgePulse = new AnimationTimer() {
        @Override public void handle(long now) {
            if (reducedMotion || scene.getScene() == null) return;
            double opacity = 0.48 + 0.34 * Math.sin(now / 420_000_000.0);
            activeEdges.forEach((id, active) -> { if (active && renderedEdges.containsKey(id)) renderedEdges.get(id).setOpacity(opacity); });
        }
    };

    public DecisionCore3D() {
        PhongMaterial core = new PhongMaterial(Color.web("#052e31"));
        core.setSpecularColor(Color.web("#55ffe1"));
        core.setSpecularPower(90);
        nucleus.setMaterial(core);
        PhongMaterial inner = new PhongMaterial(Color.web("#0b9f99"));
        inner.setSpecularColor(Color.WHITE); inner.setSpecularPower(120);
        innerCore.setMaterial(inner);
        PhongMaterial haloMaterial = new PhongMaterial(Color.web("#2de1ff"));
        halo.setMaterial(haloMaterial); halo.setDrawMode(DrawMode.LINE); halo.setOpacity(0.16);

        orbitB.setRotationAxis(Rotate.X_AXIS);
        orbitB.setRotate(62);
        orbitC.setRotationAxis(new Point3D(1, 1, 0));
        orbitC.setRotate(44);
        Group starField = starField();
        world = new Group(starField, halo, nucleus, innerCore, orbitA, orbitB, orbitC, graphLayer,
                new AmbientLight(Color.web("#29424c")), pointLight(), rimLight());
        scene = new SubScene(world, 520, 310, true, javafx.scene.SceneAntialiasing.BALANCED);
        scene.setFill(Color.web("#010608"));
        PerspectiveCamera camera = new PerspectiveCamera(true);
        camera.setTranslateZ(-520);
        camera.setNearClip(0.1);
        camera.setFarClip(4000);
        scene.setCamera(camera);
        scene.widthProperty().addListener((observable, before, width) -> camera.setTranslateZ(-520 * Math.max(1, scene.getHeight() / Math.max(100, width.doubleValue()))));
        Rotate yaw = new Rotate(0, Rotate.Y_AXIS), pitch = new Rotate(0, Rotate.X_AXIS);
        world.getTransforms().addAll(pitch, yaw);
        scene.setOnMousePressed(event -> { dragX = event.getSceneX(); dragY = event.getSceneY(); });
        scene.setOnMouseDragged(event -> {
            yaw.setAngle(yaw.getAngle() + (event.getSceneX() - dragX) * 0.5);
            pitch.setAngle(pitch.getAngle() - (event.getSceneY() - dragY) * 0.5);
            dragX = event.getSceneX(); dragY = event.getSceneY();
        });
        scene.setOnScroll(event -> { camera.setTranslateZ(Math.max(-900, Math.min(-380, camera.getTranslateZ() + event.getDeltaY()))); event.consume(); });

        spinA = spin(orbitA, 18, Rotate.Y_AXIS);
        spinB = spin(orbitB, 12, Rotate.Z_AXIS);
        spinC = spin(orbitC, 8, new Point3D(1, 1, 1));
        spinA.play(); spinB.play(); spinC.play();
        haloPulse = new ScaleTransition(Duration.seconds(2.8), halo);
        haloPulse.setFromX(0.92); haloPulse.setFromY(0.92); haloPulse.setFromZ(0.92);
        haloPulse.setToX(1.12); haloPulse.setToY(1.12); haloPulse.setToZ(1.12);
        haloPulse.setAutoReverse(true); haloPulse.setCycleCount(Animation.INDEFINITE); haloPulse.play();
        haloBreathe = new FadeTransition(Duration.seconds(2.8), halo);
        haloBreathe.setFromValue(0.08); haloBreathe.setToValue(0.28);
        haloBreathe.setAutoReverse(true); haloBreathe.setCycleCount(Animation.INDEFINITE); haloBreathe.play();
        scene.sceneProperty().addListener((observable, before, after) -> {
            if (after == null) { spinA.pause(); spinB.pause(); spinC.pause(); edgePulse.stop(); }
            else { if (!reducedMotion) { spinA.play(); spinB.play(); spinC.play(); edgePulse.start(); } }
        });
        edgePulse.start();
    }

    public SubScene node() {
        return scene;
    }

    public void bindSize(javafx.beans.value.ObservableValue<? extends Number> width,
                         javafx.beans.value.ObservableValue<? extends Number> height) {
        scene.widthProperty().bind(width);
        scene.heightProperty().bind(height);
    }

    public void setState(String state) {
        Color color = switch (state == null ? "" : state.toUpperCase()) {
            case "RUNNING" -> Color.web("#19f7a7");
            case "DEGRADED" -> Color.web("#ffb84a");
            case "HALTED", "ERROR" -> Color.web("#ff4d6d");
            default -> Color.web("#39c8ff");
        };
        PhongMaterial material = (PhongMaterial) nucleus.getMaterial();
        material.setDiffuseColor(color.deriveColor(0, 0.72, 0.45, 1));
        material.setSpecularColor(color);
        PhongMaterial inner = (PhongMaterial) innerCore.getMaterial();
        inner.setDiffuseColor(color.deriveColor(0, 0.92, 0.72, 1));
        inner.setSpecularColor(Color.WHITE);
        ((PhongMaterial) halo.getMaterial()).setDiffuseColor(color);
        double rate = "RUNNING".equalsIgnoreCase(state) ? 1.7 : 0.65;
        spinA.setRate(rate); spinB.setRate(rate); spinC.setRate(rate);
    }

    public void setSelectionListener(Consumer<String> listener) {
        selectionListener = listener == null ? node -> {} : listener;
    }

    public void setReducedMotion(boolean value) {
        reducedMotion = value;
        if (value) { spinA.pause(); spinB.pause(); spinC.pause(); haloPulse.pause(); haloBreathe.pause(); edgePulse.stop(); }
        else if (scene.getScene() != null) { spinA.play(); spinB.play(); spinC.play(); edgePulse.start(); }
    }

    /** Apply a background-prepared plan by identity, preserving unchanged scene nodes. */
    public void applyPlan(GraphRenderPlan plan) {
        if (plan == null) return;
        boolean hasMemory = !plan.nodes().isEmpty();
        if (hasMemory) {
            haloPulse.pause();
            haloBreathe.pause();
        } else {
            haloPulse.play();
            haloBreathe.play();
        }
        nucleus.setOpacity(hasMemory ? 0.0 : 1.0);
        innerCore.setOpacity(hasMemory ? 0.0 : 1.0);
        halo.setOpacity(hasMemory ? 0.0 : 0.16);
        orbitA.setOpacity(hasMemory ? 0.0 : 1.0);
        orbitB.setOpacity(hasMemory ? 0.0 : 1.0);
        orbitC.setOpacity(hasMemory ? 0.0 : 1.0);
        renderedEdges.keySet().removeIf(id -> {
            if (plan.edges().containsKey(id)) return false;
            graphLayer.getChildren().remove(renderedEdges.get(id)); activeEdges.remove(id); return true;
        });
        renderedNodes.keySet().removeIf(id -> {
            if (plan.nodes().containsKey(id)) return false;
            graphLayer.getChildren().remove(renderedNodes.get(id)); return true;
        });
        plan.edges().forEach((id, edge) -> {
            Point3D source = plan.nodes().get(edge.source()).position(), target = plan.nodes().get(edge.target()).position();
            Cylinder line = renderedEdges.computeIfAbsent(id, ignored -> { Cylinder value = new Cylinder(); graphLayer.getChildren().add(0, value); return value; });
            updateConnection(line, source, target, edge.active()); activeEdges.put(id, edge.active());
        });
        plan.nodes().forEach((id, data) -> {
            Sphere point = renderedNodes.computeIfAbsent(id, ignored -> {
                Sphere value = new Sphere(data.radius(), 14);
                value.setOnMouseClicked(event -> { selectNode(id); event.consume(); }); value.setUserData(id);
                graphLayer.getChildren().add(value); return value;
            });
            PhongMaterial material = new PhongMaterial(safeColor(data.color())); material.setSpecularColor(Color.WHITE); material.setSpecularPower(72);
            point.setMaterial(material); point.setRadius(data.radius());
            point.setTranslateX(data.position().getX()); point.setTranslateY(data.position().getY()); point.setTranslateZ(data.position().getZ());
            Tooltip.install(point, new Tooltip(data.label() + "\n" + data.type().toUpperCase()));
        });
        if (selectedId != null && !plan.nodes().containsKey(selectedId)) selectedId = null;
        fingerprint = plan.fingerprint();
    }

    public void selectNode(String id) {
        renderedNodes.forEach((key, sphere) -> {
            double scale = key.equals(id) ? 1.65 : 1.0;
            sphere.setScaleX(scale); sphere.setScaleY(scale); sphere.setScaleZ(scale);
            sphere.setOpacity(key.equals(id) ? 1.0 : 0.82);
        });
        if (renderedNodes.containsKey(id) && !id.equals(selectedId)) {
            selectedId = id;
            selectionListener.accept(id);
        }
    }

    private static void updateConnection(Cylinder line, Point3D source, Point3D target, boolean active) {
        Point3D difference = target.subtract(source);
        double height = Math.max(difference.magnitude(), 0.1);
        line.setRadius(active ? 0.75 : 0.38); line.setHeight(height);
        line.setMaterial(new PhongMaterial(active ? Color.web("#5cf2b5") : Color.web("#1d6070")));
        line.setOpacity(active ? 0.72 : 0.28);
        Point3D midpoint = source.midpoint(target);
        line.setTranslateX(midpoint.getX()); line.setTranslateY(midpoint.getY()); line.setTranslateZ(midpoint.getZ());
        Point3D axis = new Point3D(0, 1, 0).crossProduct(difference);
        double angle = Math.toDegrees(Math.acos(clamp(new Point3D(0, 1, 0).normalize().dotProduct(difference.normalize()), -1, 1)));
        line.getTransforms().clear();
        if (axis.magnitude() > 1e-6) line.getTransforms().add(new Rotate(angle, axis));
    }

    private static Color safeColor(String value) {
        try { return Color.web(value); } catch (IllegalArgumentException exception) { return Color.web("#84939a"); }
    }

    private static double clamp(double value, double minimum, double maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }

    private static Group orbit(double radius, int count, Color color) {
        Group group = new Group();
        PhongMaterial material = new PhongMaterial(color);
        material.setSelfIlluminationMap(null);
        for (int index = 0; index < count; index++) {
            double angle = Math.PI * 2 * index / count;
            Sphere point = new Sphere(index % 4 == 0 ? 3.3 : 1.7, 12);
            point.setMaterial(material);
            point.setTranslateX(Math.cos(angle) * radius);
            point.setTranslateZ(Math.sin(angle) * radius);
            group.getChildren().add(point);
        }
        return group;
    }

    private static PointLight pointLight() {
        PointLight light = new PointLight(Color.web("#7ffff0"));
        light.setTranslateX(-120); light.setTranslateY(-100); light.setTranslateZ(-180);
        return light;
    }

    private static PointLight rimLight() {
        PointLight light = new PointLight(Color.web("#7b61ff"));
        light.setTranslateX(170); light.setTranslateY(90); light.setTranslateZ(-80);
        return light;
    }

    private static Group starField() {
        Group stars = new Group();
        Random random = new Random(8072026L);
        PhongMaterial cyan = new PhongMaterial(Color.web("#2de1ff"));
        PhongMaterial violet = new PhongMaterial(Color.web("#8b7cff"));
        for (int index = 0; index < 72; index++) {
            Sphere point = new Sphere(index % 9 == 0 ? 1.25 : 0.65, 8);
            point.setMaterial(index % 5 == 0 ? violet : cyan);
            point.setOpacity(0.18 + random.nextDouble() * 0.38);
            point.setTranslateX(-250 + random.nextDouble() * 500);
            point.setTranslateY(-145 + random.nextDouble() * 290);
            point.setTranslateZ(80 + random.nextDouble() * 330);
            stars.getChildren().add(point);
        }
        return stars;
    }

    private static RotateTransition spin(Group target, double seconds, Point3D axis) {
        RotateTransition transition = new RotateTransition(Duration.seconds(seconds), target);
        transition.setAxis(axis);
        transition.setByAngle(360);
        transition.setCycleCount(Animation.INDEFINITE);
        transition.setInterpolator(javafx.animation.Interpolator.LINEAR);
        return transition;
    }
}
