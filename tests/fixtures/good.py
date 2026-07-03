"""A small, well-behaved module used as a fixture in the test suite."""


def add(a, b):
    """Return the sum of two numbers."""
    return a + b


def classify(value):
    """Return a label describing the sign of value."""
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "zero"
