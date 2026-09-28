# BDD feature tests

The Behave scenarios run in a dedicated Docker Compose project named `webcaf-behave`. The project has its own
PostgreSQL database, Valkey instance, network and volumes. It does not publish host ports, so it can run while the
normal development stack is running.

`make behave` removes stale feature-test resources before the run, applies committed migrations, creates scenario
data and removes the test containers, network and volumes afterwards. Cleanup runs whether the scenarios pass or
fail. The normal development database, containers and Python virtual environment are not used.

## Running the tests

```shell
# Run all scenarios in headless Chromium.
make behave

# Run a selected feature.
make behave FEATURE_TEST_ARGS="-i admin-login.feature"
```

The HTML report is written to `reports/report.html`. Failed scenarios write screenshots and page HTML to
`artifacts/`, and a failed run writes the Compose service logs to `artifacts/docker-compose.log` before cleanup.

The containerized workflow is headless. Do not run Behave directly against the normal development Compose stack,
because direct execution bypasses the disposable test database. Use focused features and the generated failure
artifacts when debugging locally.

If a run is forcibly terminated before its cleanup completes, the next `make behave` removes any stale isolated test
resources before creating a fresh environment. They can also be removed explicitly with:

```shell
make behave-clean
```
