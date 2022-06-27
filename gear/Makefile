include ../config.mk

EXTRA_PYTHONPATH := ../hail/python:../web_common
PYTHON := PYTHONPATH=$${PYTHONPATH:+$${PYTHONPATH}:}$(EXTRA_PYTHONPATH) python3
BLACK := $(PYTHON) -m black . --line-length=120 --skip-string-normalization

TOKEN = $(shell cat /dev/urandom | LC_ALL=C tr -dc 'a-z0-9' | head -c 12)
GEAR_IMAGE = $(DOCKER_PREFIX)/gear:$(TOKEN)

.PHONY: check
check:
	$(PYTHON) -m flake8 gear
	$(PYTHON) -m pylint --rcfile ../pylintrc gear --score=n
	$(PYTHON) -m isort . --check-only --diff
	$(BLACK) --check --diff

.PHONY: generate-pip-lockfile
generate-pip-lockfile:
	../generate-linux-pip-lockfile.sh gear

.PHONY: gear gear-image-ref
gear: gear-image-ref
gear-image-ref:
	python3 ../ci/jinja2_render.py '{"hailgenetics_hailtop_slim_image":{"image":"hailtop-slim"}}' Dockerfile Dockerfile.out
	../docker-build.sh .. gear/Dockerfile.out $(GEAR_IMAGE)
	echo $(GEAR_IMAGE) > $@
