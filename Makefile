BEHAVE_COMPOSE = docker compose --project-name webcaf-behave -f docker-compose.yml -f docker-compose.feature-tests.yml
BEHAVE_DEX_COMPOSE = docker compose --project-name webcaf-behave-dex --profile dex -f docker-compose.yml -f docker-compose.feature-tests.yml -f docker-compose.dex.yml -f docker-compose.dex-feature-tests.yml
DEV_DEX_COMPOSE = docker compose --profile dex -f docker-compose.yml -f docker-compose.dex.yml
TEST_DATABASE_URL = postgresql://webcaf:webcaf@postgres:5432/webcaf  # pragma: allowlist secret

.PHONY: up-devserver up-devserver-nodebug shell management-shell clear-db test build behave behave_dex behave-clean up_dex up_one_login_simulator one_login_user

up-devserver:
	docker compose -f docker-compose.yml run --rm --service-ports --entrypoint "python manage.py runserver 0.0.0.0:8000" web

up-devserver-nodebug:
	docker compose -f docker-compose.yml run --rm --service-ports --env DEBUG=False --entrypoint "python manage.py runserver 0.0.0.0:8000" web

shell:
	docker compose exec web bash

management-shell:
	docker compose run --rm --entrypoint bash init

clear-db:
	docker compose down --volumes --remove-orphans

test:
	docker compose run --rm --service-ports --remove-orphans \
		--env DATABASE_URL=$(TEST_DATABASE_URL) \
		--env DATABASE_ASSUME_ROLE= \
		--entrypoint "poetry run pytest --html=reports/pytest-report.html --self-contained-html" web
	docker compose down
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
	exit "$$status"

up_dex:
	$(DEV_DEX_COMPOSE) up -d --wait web

up_one_login_simulator:
	docker compose up -d --wait postgres redis one-login-simulator
	$(MAKE) one_login_user PRESET=alice

one_login_user:
	poetry run python -m features.one_login_simulator $(or $(PRESET),alice)
