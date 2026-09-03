package com.mashcorp.jarvis;

import javafx.animation.Animation;
import javafx.animation.RotateTransition;
import javafx.geometry.Point3D;
import javafx.scene.AmbientLight;
import javafx.scene.Group;
import javafx.scene.PerspectiveCamera;
import javafx.scene.PointLight;
import javafx.scene.SubScene;
import javafx.scene.paint.Color;
import javafx.scene.paint.PhongMaterial;
import javafx.scene.shape.Sphere;
import javafx.scene.transform.Rotate;
import javafx.util.Duration;

/** Lightweight native 3D decision core. It visualizes state; it never computes orders. */
public final class DecisionCore3D {
    private final Group orbitA = orbit(135, 18, Color.web("#19f7a7"));
    private final Group orbitB = orbit(105, 14, Color.web("#39c8ff"));
    private final Group orbitC = orbit(72, 10, Color.web("#ffb84a"));
    private final Sphere nucleus = new Sphere(42, 40);
    private final SubScene scene;
    private final RotateTransition spinA;
    private final RotateTransition spinB;
    private final RotateTransition spinC;
    private double dragX;
    private double dragY;

    public DecisionCore3D() {
        PhongMaterial core = new PhongMaterial(Color.web("#052e31"));
        core.setSpecularColor(Color.web("#55ffe1"));
        core.setSpecularPower(90);
        nucleus.setMaterial(core);

        orbitB.setRotationAxis(Rotate.X_AXIS);
        orbitB.setRotate(62);
        orbitC.setRotationAxis(new Point3D(1, 1, 0));
        orbitC.setRotate(44);
        Group world = new Group(nucleus, orbitA, orbitB, orbitC,
                new AmbientLight(Color.web("#34515a")), pointLight());
        scene = new SubScene(world, 520, 310, true, javafx.scene.SceneAntialiasing.BALANCED);
        scene.setFill(Color.web("#03090c"));
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
        scene.sceneProperty().addListener((observable, before, after) -> {
            if (after == null) { spinA.pause(); spinB.pause(); spinC.pause(); }
            else { spinA.play(); spinB.play(); spinC.play(); }
        });
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
        double rate = "RUNNING".equalsIgnoreCase(state) ? 1.7 : 0.65;
        spinA.setRate(rate); spinB.setRate(rate); spinC.setRate(rate);
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

    private static RotateTransition spin(Group target, double seconds, Point3D axis) {
        RotateTransition transition = new RotateTransition(Duration.seconds(seconds), target);
        transition.setAxis(axis);
        transition.setByAngle(360);
        transition.setCycleCount(Animation.INDEFINITE);
        transition.setInterpolator(javafx.animation.Interpolator.LINEAR);
        return transition;
    }
}
