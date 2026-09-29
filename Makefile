up-devserver:
	docker compose -f docker-compose.yml run --rm --service-ports --entrypoint "python manage.py runserver 0.0.0.0:8000" web

up-devserver-nodebug:
	docker compose -f docker-compose.yml run --rm --service-ports --env DEBUG=False --entrypoint "python manage.py runserver 0.0.0.0:8000" web

shell:
	docker compose exec web bash

clear-db:
	docker compose down && docker container prune -f && docker volume rm domains-webcaf_postgres-data

test:
	docker compose run --rm --service-ports --remove-orphans --entrypoint "poetry run pytest --html=reports/pytest-report.html --self-contained-html" web
	docker compose down
build:
	BUILDKIT_PROGRESS=plain docker compose build

behave:
	FEATURE_TEST_ARGS="--tags=~one_login $(FEATURE_TEST_ARGS)" docker compose -f docker-compose.yml -f docker-compose.feature-tests.yml up --build --abort-on-container-exit --remove-orphans --exit-code-from feature-tests feature-tests
	docker compose down

behave_one_login:
	FEATURE_TEST_ARGS="--tags=one_login_compatible $(FEATURE_TEST_ARGS)" docker compose --profile one-login-simulator -f docker-compose.yml -f docker-compose.feature-tests.yml -f docker-compose.one-login-feature-tests.yml up --build --abort-on-container-exit --remove-orphans --exit-code-from feature-tests feature-tests
	docker compose --profile one-login-simulator -f docker-compose.yml -f docker-compose.feature-tests.yml -f docker-compose.one-login-feature-tests.yml down

up_dex:
	#	Bring up DEX and Valkey for local development
	docker compose -f docker-compose.yml up -d --wait oauth redis

up_one_login_simulator:
	docker compose --profile one-login-simulator up -d --wait postgres redis one-login-simulator
	$(MAKE) one_login_user PRESET=alice

one_login_user:
	poetry run python -m features.one_login_simulator $(or $(PRESET),alice)
