"""Pytest configuration for test suite."""
import uuid
import pytest


def pytest_addoption(parser):
    """Add custom command-line options to pytest."""
    parser.addoption(
        "--fixed-ids",
        action="store_true",
        default=False,
        help="Use fixed car IDs instead of random UUIDs (useful for frontend testing)",
    )


@pytest.fixture(scope="session")
def use_fixed_ids(request):
    """Fixture that returns whether to use fixed car IDs."""
    return request.config.getoption("--fixed-ids")


@pytest.fixture
def get_car_id(use_fixed_ids):
    """Fixture that returns a function to generate car IDs."""
    def _get_car_id(base_name: str) -> str:
        """Generate car ID: random UUID by default, or fixed name with --fixed-ids flag."""
        if use_fixed_ids:
            return base_name
        return f"{base_name}-{str(uuid.uuid4())[:8]}"
    
    return _get_car_id
