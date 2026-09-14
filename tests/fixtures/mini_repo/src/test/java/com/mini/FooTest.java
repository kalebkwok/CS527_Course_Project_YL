package com.mini;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class FooTest {
    private Store store = new Store();

    @Test
    void testProcessRoundTrips() throws Exception {
        Foo foo = new Foo();
        Bar bar = Bar.of("a");
        assertEquals(1, foo.process(bar, 1));
    }
}
