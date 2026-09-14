package com.mini;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class BarTest {
    @Test
    void testOfParsesValue() {
        Bar bar = Bar.of("a");
        assertEquals("a", bar.getValue());
    }
}
