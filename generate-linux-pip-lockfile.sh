#!/bin/bash
# Some service dependencies are linux-specific and the lockfile
# would differ when generated on MacOS, so we generate the lockfile
# on a linux image.
app=$1
docker run --rm -it \
    -v $HAIL:/hail \
    python:3.7-slim \
    /bin/bash -c "pip install pip-tools && cd hail && pip-compile --upgrade $app/requirements.txt --output-file=$app/linux-pinned-requirements.txt"
