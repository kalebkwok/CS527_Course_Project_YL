package com.sample;

/**
 * Builder for {@link Widget}. Per SPEC §4.2 a *builder* is a method returning a type named
 * {@code *Builder} whose {@code build()} returns the type being built — here
 * {@code Widget#builder()} returns this class and {@code build()} returns a {@link Widget}.
 *
 * <p>Consequently this class has no builder methods of its own: {@code build()} returns
 * {@code Widget}, not {@code WidgetBuilder}.
 */
public class WidgetBuilder {

    private String name = "";
    private int size;

    public WidgetBuilder name(String name) {
        this.name = name;
        return this;
    }

    public WidgetBuilder size(int size) {
        this.size = size;
        return this;
    }

    public Widget build() {
        return new Widget(name, size);
    }
}
