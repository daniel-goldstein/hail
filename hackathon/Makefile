.PHONY: wheel
wheel:
	$(MAKE) -C ../hail/hail wheel
	cp ../hail/build/deploy/dist/*.whl ./

run:
	python3 -m http.server
