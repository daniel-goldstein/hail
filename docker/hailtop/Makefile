include ../../config.mk

TOKEN := $(shell cat /dev/urandom | LC_ALL=C tr -dc 'a-z0-9' | head -c 12)
HAILTOP_IMAGE := $(DOCKER_PREFIX)/hailtop:$(TOKEN)

.PHONY: build hailtop-image-ref
build: hailtop-image-ref
hailtop-image-ref:
	python3 ../../ci/jinja2_render.py '{"global":{"docker_prefix":"$(DOCKER_PREFIX)"}}' Dockerfile Dockerfile.out
	../../docker-build.sh ../.. docker/hailtop/Dockerfile.out $(HAILTOP_IMAGE)
	echo $(HAILTOP_IMAGE) > $@
