BEHAVE_COMPOSE = docker compose --project-name webcaf-behave -f docker-compose.yml -f docker-compose.feature-tests.yml
BEHAVE_DEX_COMPOSE = docker compose --project-name webcaf-behave-dex --profile dex -f docker-compose.yml -f docker-compose.feature-tests.yml -f docker-compose.dex.yml -f docker-compose.dex-feature-tests.yml
DEV_ONE_LOGIN_COMPOSE = docker compose --profile one-login -f docker-compose.yml -f docker-compose.one-login.yml
BEHAVE_HEADED_COMPOSE = docker compose --project-name webcaf-behave-headed -f docker-compose.yml
DEV_DEX_COMPOSE = docker compose --profile dex -f docker-compose.yml -f docker-compose.dex.yml
BEHAVE_SLOW_MO ?= 250
BEHAVE_PWDEBUG ?= 0

.PHONY: up-devserver up-devserver-nodebug shell clear-db test build behave behave-headed behave_dex behave-clean up_dex up_one_login_simulator one_login_user

up-devserver:
	docker compose -f docker-compose.yml run --rm --service-ports --entrypoint "python manage.py runserver 0.0.0.0:8000" web

up-devserver-nodebug:
	docker compose -f docker-compose.yml run --rm --service-ports --env DEBUG=False --entrypoint "python manage.py runserver 0.0.0.0:8000" web

shell:
	docker compose exec web bash

clear-db:
	docker compose down && docker container prune -f && docker volume rm domains-webcaf_postgres-data

test:
	$(DEV_ONE_LOGIN_COMPOSE) run --rm --remove-orphans -e SSO_MODE=one-login --entrypoint "pytest --html=reports/pytest-report.html --self-contained-html" web
	$(DEV_ONE_LOGIN_COMPOSE) down

lint:
	#	Check formatting and linting without changing anything.
	uv run pre-commit run --hook-stage manual ruff-check-only --all-files
	uv run pre-commit run --hook-stage manual ruff-format-check --all-files

format:
	#	Apply Ruff's fixes and formatting across the repository.
	uv run pre-commit run ruff-check --all-files || true
	uv run pre-commit run ruff-format --all-files || true

build:
	BUILDKIT_PROGRESS=plain docker compose build

behave:
	$(BEHAVE_COMPOSE) down --volumes --remove-orphans
	mkdir -p artifacts reports
	@status=0; cleanup_status=0; \
	cleanup() { \
		if [ "$$status" -ne 0 ]; then \
			$(BEHAVE_COMPOSE) logs --no-color > artifacts/one-login-docker-compose.log 2>&1 || true; \
		fi; \
		$(BEHAVE_COMPOSE) down --volumes --remove-orphans || cleanup_status=$$?; \
	}; \
	trap 'status=130; cleanup; trap - INT; exit "$$status"' INT; \
	trap 'status=143; cleanup; trap - TERM; exit "$$status"' TERM; \
	FEATURE_TEST_ARGS="--tags=~dex $(FEATURE_TEST_ARGS)" $(BEHAVE_COMPOSE) up --build --abort-on-container-exit --remove-orphans --exit-code-from feature-tests feature-tests || status=$$?; \
	cleanup; \
	if [ "$$status" -ne 0 ]; then exit "$$status"; fi; \
	exit "$$cleanup_status"

behave-headed:
	$(BEHAVE_HEADED_COMPOSE) down --volumes --remove-orphans
	mkdir -p artifacts reports
	@status=0; cleanup_status=0; \
	cleanup() { \
		if [ "$$status" -ne 0 ]; then \
			$(BEHAVE_HEADED_COMPOSE) logs --no-color > artifacts/one-login-headed-docker-compose.log 2>&1 || true; \
		fi; \
		$(BEHAVE_HEADED_COMPOSE) down --volumes --remove-orphans || cleanup_status=$$?; \
	}; \
	trap 'status=130; cleanup; trap - INT; exit "$$status"' INT; \
	trap 'status=143; cleanup; trap - TERM; exit "$$status"' TERM; \
	ONE_LOGIN_SIMULATOR_INTERACTIVE_MODE=false $(BEHAVE_HEADED_COMPOSE) up -d --build --wait web || status=$$?; \
	if [ "$$status" -eq 0 ]; then \
		DEBUG=True \
		DD_TRACE_ENABLED=false \
		SECRET_KEY=unused \
		DJANGO_SETTINGS_MODULE=webcaf.settings \
		DATABASE_URL=postgresql://webcaf:webcaf@localhost:54321/webcaf \
		REDIS_URL=redis://localhost:6379/0 \
		SSO_MODE=one-login \
		GOV_UK_ONE_LOGIN_CLIENT_ID=webcaf-local \
		GOV_UK_ONE_LOGIN_PRIVATE_KEY_PATH=tests/fixtures/one-login-simulator-private-key.pem \
		GOV_UK_ONE_LOGIN_OPENID_CONFIG_URL=http://localhost:3000/.well-known/openid-configuration \
		GOV_UK_ONE_LOGIN_SCOPE="openid email" \
		GOV_UK_ONE_LOGIN_ENVIRONMENT=integration \
		ONE_LOGIN_SIMULATOR_URL=http://localhost:3000 \
		NOTIFY_API_KEY= \
		NOTIFY_API_TIMEOUT_SECONDS=10 \
		RUN_GOVUK_NOTIFY_INTEGRATION_TESTS=false \
		PWDEBUG=$(BEHAVE_PWDEBUG) \
		uv run python -m behave --no-capture --no-capture-stderr -f behave_html_formatter:HTMLFormatter -o reports/report.html -D base_url=http://localhost:8010 -D headless_testing=false -D slow_mo=$(BEHAVE_SLOW_MO) --tags=~dex $(FEATURE_TEST_ARGS) || status=$$?; \
	fi; \
	cleanup; \
	if [ "$$status" -ne 0 ]; then exit "$$status"; fi; \
	exit "$$cleanup_status"

behave_dex:
	$(BEHAVE_DEX_COMPOSE) down --volumes --remove-orphans
	mkdir -p artifacts reports
	@status=0; cleanup_status=0; \
	cleanup() { \
		if [ "$$status" -ne 0 ]; then \
			$(BEHAVE_DEX_COMPOSE) logs --no-color > artifacts/dex-docker-compose.log 2>&1 || true; \
		fi; \
		$(BEHAVE_DEX_COMPOSE) down --volumes --remove-orphans || cleanup_status=$$?; \
	}; \
	trap 'status=130; cleanup; trap - INT; exit "$$status"' INT; \
	trap 'status=143; cleanup; trap - TERM; exit "$$status"' TERM; \
	FEATURE_TEST_ARGS="--tags=dex $(FEATURE_TEST_ARGS)" $(BEHAVE_DEX_COMPOSE) up --build --abort-on-container-exit --remove-orphans --exit-code-from feature-tests feature-tests || status=$$?; \
	cleanup; \
	if [ "$$status" -ne 0 ]; then exit "$$status"; fi; \
	exit "$$cleanup_status"

behave-clean:
	@status=0; \
	$(BEHAVE_COMPOSE) down --volumes --remove-orphans || status=1; \
	$(BEHAVE_DEX_COMPOSE) down --volumes --remove-orphans || status=1; \
	$(BEHAVE_HEADED_COMPOSE) down --volumes --remove-orphans || status=1; \
	exit "$$status"

up_dex:
	$(DEV_DEX_COMPOSE) up -d --wait web

up_one_login:
	$(DEV_ONE_LOGIN_COMPOSE) up -d --wait web
	$(MAKE) one_login_user PRESET=alice

up_one_login_simulator:
	$(DEV_ONE_LOGIN_COMPOSE) up -d --wait postgres redis one-login-simulator
	$(MAKE) one_login_user PRESET=alice

one_login_user:
	uv run python -m features.one_login_simulator $(or $(PRESET),alice)
