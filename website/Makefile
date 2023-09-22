include ../config.mk

.PHONY: build push run run-docker deploy clean

build:
	$(MAKE) -C .. website-image

run:
	$(MAKE) -C .. website/website/docs
	HAIL_DOMAIN=localhost PORT=80 python3 -m website local

.PHONY: typesense-scraper
typesense-scraper:
	docker run \
		-e TYPESENSE_API_KEY=xyz \
		-e TYPESENSE_HOST=host.docker.internal \
		-e TYPESENSE_PORT=8108 \
		-e TYPESENSE_PROTOCOL=http \
		-e 'CONFIG=$(shell cat typesense/config.json | jq -r tostring)' \
		typesense/docsearch-scraper:0.8.0

run-docker: build
	WEBSITE_IMAGE=$(shell cat ../website-image) \
	TYPESENSE_SCRAPER_CONFIG='$(shell cat typesense/config.json | jq -r tostring)' \
	docker compose --profile scrape up

push:
	$(MAKE) -C .. pushed-private-website-image

deploy: push
	! [ -z $(NAMESPACE) ]  # call this like: make deploy NAMESPACE=default
	python3 ../ci/jinja2_render.py '{"default_ns":{"name":"$(NAMESPACE)"},"code":{"sha":"$(shell git rev-parse --short=12 HEAD)"},"deploy":$(DEPLOY),"scope":"$(SCOPE)","website_image":{"image":"'$$(cat ../pushed-private-website-image)'"}}' deployment.yaml deployment.yaml.out
	kubectl -n $(NAMESPACE) apply -f deployment.yaml.out

clean:
	rm -rf deployment.yaml website/docs website/static/css
