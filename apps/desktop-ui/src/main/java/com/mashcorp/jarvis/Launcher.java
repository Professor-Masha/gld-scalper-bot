package com.mashcorp.jarvis;

/** Plain launcher keeps JavaFX startup compatible with Maven and packaged runtimes. */
public final class Launcher {
    private Launcher() {}

    public static void main(String[] args) {
        JarvisApplication.launchApplication(args);
    }
}
