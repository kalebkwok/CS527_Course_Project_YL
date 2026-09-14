package com.sample;

import java.io.IOException;
import java.nio.file.Path;
import java.util.Objects;

/**
 * Sample focal type for the indexer acceptance check (SPEC §12, "3-file sample project").
 *
 * <p>The sample deliberately contains:
 * <ul>
 *   <li>a public and a package-private constructor, plus a private one that MUST NOT be indexed;</li>
 *   <li>a static factory {@link #of(String)} and an instance method returning the same type
 *       ({@link #copy()}, which is NOT a factory);</li>
 *   <li>a builder entry point {@link #builder()} whose returned type has {@code build()}: Widget;</li>
 *   <li>a public static final singleton field {@link #DEFAULT} and a static field of another type
 *       ({@link #VERSION}, which is NOT a singleton);</li>
 *   <li>a nested enum with two constants;</li>
 *   <li>the focal method {@link #grow(int)}, which reads the field {@code size}.</li>
 * </ul>
 */
public class Widget {

    public static final Widget DEFAULT = new Widget("default", 0);
    public static final String VERSION = "1.0";

    private final String name;
    private final int size;
    private int counter;

    public Widget(String name, int size) {
        this.name = name;
        this.size = size;
    }

    Widget(String name) {
        this(name, 0);
    }

    private Widget() {
        this("", 0);
    }

    public static Widget of(String name) {
        return new Widget(name, name.length());
    }

    public static WidgetBuilder builder() {
        return new WidgetBuilder();
    }

    public Widget copy() {
        return new Widget(name, size);
    }

    public String name() {
        return name;
    }

    public int getSize() {
        return size;
    }

    public int measure(Path source) throws IOException {
        return size + source.getNameCount();
    }

    public int grow(int delta) {
        if (delta < 0) {
            throw new IllegalArgumentException("delta must not be negative");
        }
        return size + delta;
    }

    public int bump() {
        counter = counter + 1;
        return counter;
    }

    @Override
    public boolean equals(Object other) {
        if (!(other instanceof Widget)) {
            return false;
        }
        Widget that = (Widget) other;
        return size == that.size && Objects.equals(name, that.name);
    }

    @Override
    public int hashCode() {
        return Objects.hash(name, size);
    }

    @Override
    public String toString() {
        return "Widget[" + name + ", " + size + "]";
    }

    private static String secret() {
        return "s";
    }

    public enum Color {
        RED,
        GREEN
    }
}
