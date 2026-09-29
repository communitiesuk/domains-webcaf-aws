# BDD Testing framework

The containerised feature tests support both local identity providers. `make behave` runs the 26 shared application
scenarios with DEX plus two provider-independent Django admin scenarios. `make behave_one_login` runs the same 26
application scenarios and four One Login-specific scenarios with the GOV.UK One Login simulator.

The behave.ini file contains the names of the user emails and the organisations that can be used in the testing.
This is fixed so that the cleanup process can reset the database to its orignal state before each scenario is run.

## Disable headless mode
Sometimes it is easy to debug when we see what is displayed on the browser. To view the browser window, we have to
disable the headless mode by providing a user data parameter.

You will need to add ```-D headless_testing=false``` to the main command to get this set up.

Command to run.
```shell
# This runs the shared scenarios with DEX plus the provider-independent Django admin scenarios.
make behave

# This runs the shared scenarios and One Login-specific scenarios with the simulator.
make behave_one_login

# This runs only the four One Login-specific scenarios.
make behave_one_login FEATURE_TEST_ARGS="--tags=one_login"

# This runs a selected provider-independent admin feature.
make behave FEATURE_TEST_ARGS="-i admin-login.feature"

# To see the browser window, start the services and invoke Behave directly.
docker compose -f docker-compose.yml up -d
export DATABASE_URL=postgresql://webcaf:webcaf@localhost:54321/webcaf # pragma: allowlist secret
SSO_MODE=localhost \
  DATABASE_URL="$DATABASE_URL" \
  SECRET_KEY=unused \
  poetry run behave --tags=~one_login -D headless_testing=false
```
