import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_repository_backend", "typo_database"),
        ("semantic_index_backend", "typo_index"),
        ("extraction_provider", "typo_provider"),
    ],
)
def test_invalid_backend_configuration_fails_loudly(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "background_poll_interval_seconds",
        "background_max_attempts",
        "background_lease_seconds",
    ],
)
def test_invalid_worker_configuration_fails_loudly(field):
    with pytest.raises(ValueError):
        Settings(**{field: 0})