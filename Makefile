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
	@status=0; FEATURE_TEST_ARGS="--tags=~dex $(FEATURE_TEST_ARGS)" docker compose -p webcaf-behave -f docker-compose.yml -f docker-compose.feature-tests.yml up --build --abort-on-container-exit --remove-orphans --exit-code-from feature-tests feature-tests || status=$$?; if [ $$status -ne 0 ]; then mkdir -p artifacts; docker compose -p webcaf-behave -f docker-compose.yml -f docker-compose.feature-tests.yml logs --no-color > artifacts/one-login-docker-compose.log; fi; docker compose -p webcaf-behave -f docker-compose.yml -f docker-compose.feature-tests.yml down -v; exit $$status

behave_dex:
	@status=0; FEATURE_TEST_ARGS="--tags=dex $(FEATURE_TEST_ARGS)" docker compose -p webcaf-behave-dex --profile dex -f docker-compose.yml -f docker-compose.feature-tests.yml -f docker-compose.dex.yml -f docker-compose.dex-feature-tests.yml up --build --abort-on-container-exit --remove-orphans --exit-code-from feature-tests feature-tests || status=$$?; if [ $$status -ne 0 ]; then mkdir -p artifacts; docker compose -p webcaf-behave-dex --profile dex -f docker-compose.yml -f docker-compose.feature-tests.yml -f docker-compose.dex.yml -f docker-compose.dex-feature-tests.yml logs --no-color > artifacts/dex-docker-compose.log; fi; docker compose -p webcaf-behave-dex --profile dex -f docker-compose.yml -f docker-compose.feature-tests.yml -f docker-compose.dex.yml -f docker-compose.dex-feature-tests.yml down -v; exit $$status

behave-clean:
	docker compose -p webcaf-behave -f docker-compose.yml -f docker-compose.feature-tests.yml down -v --remove-orphans
	docker compose -p webcaf-behave-dex --profile dex -f docker-compose.yml -f docker-compose.feature-tests.yml -f docker-compose.dex.yml -f docker-compose.dex-feature-tests.yml down -v --remove-orphans

up_dex:
	docker compose --profile dex -f docker-compose.yml -f docker-compose.dex.yml up -d --wait web

up_one_login_simulator:
	docker compose up -d --wait postgres redis one-login-simulator
	$(MAKE) one_login_user PRESET=alice

one_login_user:
	poetry run python -m features.one_login_simulator $(or $(PRESET),alice)
