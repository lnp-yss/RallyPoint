"""Optional web package; database helpers do not require Flask to import."""


def create_app(overrides=None):
    """Load the Flask application only when a web process explicitly requests it."""
    from .application import create_app as factory

    return factory(overrides)
