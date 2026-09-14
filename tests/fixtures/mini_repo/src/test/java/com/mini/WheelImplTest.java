package com.mini;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

import org.junit.jupiter.api.Test;

class WheelImplTest {
    @Test
    void testSpinDelegates() {
        Wheel delegate = mock(Wheel.class);
        new WheelImpl(delegate).spin();
        verify(delegate).spin();
    }
}
