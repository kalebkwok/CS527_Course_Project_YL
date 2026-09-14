package com.mini;

public class Bar {
    private final String value;

    private Bar(String value) {
        this.value = value;
    }

    public static Bar of(String value) {
        return new Bar(value);
    }

    public String getValue() {
        return value;
    }
}
