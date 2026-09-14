package com.mini;

import java.util.LinkedHashMap;
import java.util.Map;

public class Store {
    private final Map<String, Integer> values = new LinkedHashMap<>();

    public Store() {
    }

    public void put(String key, int value) {
        values.put(key, value);
    }

    public int size() {
        return values.size();
    }
}
