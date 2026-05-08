# tests/conftest.py
# pytest configuration for the Kinetic Ingestor test suite.


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests that run the full pipeline "
        "(deselect with: pytest -m 'not integration')",
    )
