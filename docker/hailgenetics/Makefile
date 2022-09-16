include ../../config.mk

.PHONY: publish-python-dill

BATCH_CONTAINER_REPOSITORIES=$(shell kubectl get secret global-config --template={{.data.batch_container_repositories}} | base64 --decode | jq -r 'values | .[]')

publish-python-dill:
	DOCKER_PREFIX=$(DOCKER_PREFIX) bash python-dill/push.sh

mirror-dockerhub-images:
	DOCKER_PREFIX=$(DOCKER_PREFIX) \
	HAIL_PIP_VERSION=$(shell cat ../../hail/python/hail/hail_pip_version) \
	./mirror_images.sh $(BATCH_CONTAINER_REPOSITORIES)
