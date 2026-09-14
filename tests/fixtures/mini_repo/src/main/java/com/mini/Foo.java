package com.mini;

import java.io.IOException;

public class Foo {
    private final Store store = new Store();

    public Foo() {
    }

    public int process(Bar bar, int n) throws IOException {
        if (n < 0) {
            throw new IOException("negative delta");
        }
        store.put(bar.getValue(), n);
        return store.size() + n;
    }

    public int size() {
        return store.size();
    }
}
