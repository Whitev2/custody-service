.PHONY: test test-cov test-fast lint format install-test

# Install test dependencies
install-test:
	pip install -e ".[test]"

# Run all tests
test:
	pytest tests/ -v

# Run tests with coverage report
test-cov:
	pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=html

# Run tests fast (skip slow tests)
test-fast:
	pytest tests/ -v -m "not slow"

# Run only unit tests
test-unit:
	pytest tests/test_models.py tests/test_schemas.py tests/test_services.py -v

# Run only API tests
test-api:
	pytest tests/test_api_*.py tests/test_health.py -v

# Run only integration tests
test-integration:
	pytest tests/test_integration.py -v -m integration

# Run linter
lint:
	ruff check app/ tests/

# Format code
format:
	ruff format app/ tests/
	black app/ tests/

# Type checking (requires mypy)
typecheck:
	mypy app/ --ignore-missing-imports

