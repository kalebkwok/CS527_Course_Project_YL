package com.mini;

public class WheelImpl implements Wheel {
    private final Wheel delegate;

    public WheelImpl(Wheel delegate) {
        this.delegate = delegate;
    }

    @Override
    public void spin() {
        delegate.spin();
    }
}
