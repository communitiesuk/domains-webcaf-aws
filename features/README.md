# BDD Testing framework

One Login is the default identity provider for the containerised feature tests. `make behave` runs 26 application
scenarios, four focused One Login scenarios, and two provider-independent Django admin scenarios. `make behave_dex`
runs only the two scenarios tagged `@dex` that explicitly verify DEX login and logout.

The `behave.ini` file contains the user emails and organisations removed between scenarios so each scenario starts from
a predictable database state.

## Commands

```shell
# Run the default One Login suite.
make behave

# Run only the four focused One Login scenarios.
make behave FEATURE_TEST_ARGS="--tags=one_login"

# Run the focused DEX authentication scenarios.
make behave_dex

# Run a selected provider-independent feature with One Login services available.
make behave FEATURE_TEST_ARGS="-i admin-login.feature"
```

Feature tests run headlessly by default. Pass `-D headless_testing=false` through `FEATURE_TEST_ARGS` to display the
browser when the environment supports headed Playwright:

```shell
make behave FEATURE_TEST_ARGS="-D headless_testing=false"
```
