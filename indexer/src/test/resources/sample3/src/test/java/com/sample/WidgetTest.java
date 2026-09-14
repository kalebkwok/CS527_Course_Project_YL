package com.sample;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

/**
 * The single test file of the sample. Both test methods call the focal method
 * {@code Widget#grow(int)}; the field {@code widget} is a fixture with an initializer, and
 * {@code widgetNamed} is a test utility (a Helper recipe source, SPEC §4.2 {@code test_helpers}).
 */
class WidgetTest {

    private final Widget widget = Widget.of("abc");

    @Test
    void growAddsDeltaToSize() {
        assertEquals(5, widget.grow(2));
        assertEquals(3, widget.getSize());
    }

    @Test
    void growRejectsNegativeDelta() {
        assertThrows(IllegalArgumentException.class, () -> widget.grow(-1));
    }

    static Widget widgetNamed(String name) {
        return Widget.of(name);
    }
}
