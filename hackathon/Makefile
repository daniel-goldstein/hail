.PHONY: wheel build-jupyterlite serve run
wheel:
	$(MAKE) -C ../hail wheel

build-jupyterlite:
	rm -rf _output
	jupyter lite build --piplite-wheels ../hail/build/deploy/dist/*.whl

serve:
	jupyter lite serve

run: wheel build-jupyterlite serve
