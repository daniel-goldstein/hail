include ../config.mk

.PHONY: deploy

deploy:
	! [ -z $(NAMESPACE) ]  # call this like: make deploy NAMESPACE=default
	python3 ../ci/jinja2_render.py '{"deploy":$(DEPLOY),"default_ns":{"name":"$(NAMESPACE)"},"global":{"domain":"$(DOMAIN)","docker_prefix":"$(DOCKER_PREFIX)"}}' zipkin.yaml zipkin.yaml.out
	kubectl -n $(NAMESPACE) apply -f zipkin.yaml.out
