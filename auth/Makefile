include ../config.mk

TOKEN = $(shell cat /dev/urandom | LC_ALL=C tr -dc 'a-z0-9' | head -c 12)

AUTH_IMAGE := $(DOCKER_PREFIX)/auth:$(TOKEN)

EXTRA_PYTHONPATH := ../hail/python:../gear:../web_common
PYTHON := PYTHONPATH=$${PYTHONPATH:+$${PYTHONPATH}:}$(EXTRA_PYTHONPATH) python3
BLACK := $(PYTHON) -m black . --line-length=120 --skip-string-normalization

.PHONY: check
check:
	$(PYTHON) -m flake8 auth
	$(PYTHON) -m pylint --rcfile ../pylintrc auth --score=n
	$(PYTHON) -m isort . --check-only --diff
	$(BLACK) --check --diff
	curlylint .
	bash ../check-sql.sh

.PHONY: build
build:
	$(MAKE) -C ../gear build
	python3 ../ci/jinja2_render.py '{"gear_image":{"image":"'$$(cat ../gear/gear-image-ref)'"}}' Dockerfile Dockerfile.out
	../docker-build.sh .. auth/Dockerfile.out $(AUTH_IMAGE)

.PHONY: deploy
deploy: build
	! [ -z $(NAMESPACE) ]  # call this like: make deploy NAMESPACE=default
	kubectl -n $(NAMESPACE) apply -f auth-driver-service-account.yaml
	python3 ../ci/jinja2_render.py '{"code":{"sha":"$(shell git rev-parse --short=12 HEAD)"},"deploy":$(DEPLOY),"default_ns":{"name":"$(NAMESPACE)"},"auth_image":{"image":"$(AUTH_IMAGE)"},"auth_database":{"user_secret_name":"sql-auth-user-config"}}' deployment.yaml deployment.yaml.out
	kubectl -n $(NAMESPACE) apply -f deployment.yaml.out

.PHONY: generate-pip-lockfile
generate-pip-lockfile:
	../generate-linux-pip-lockfile.sh auth

.PHONY:
clean:
	rm -f Dockerfile.out deployment.yaml.out
