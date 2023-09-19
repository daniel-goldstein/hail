#!/bin/bash

set -ex

package=$1
reqs=$package/requirements.txt
pinned_reqs=$package/pinned-requirements.txt
command="pip-compile --strip-extras --upgrade $reqs --output-file=$pinned_reqs"

# Some service dependencies are linux-specific and the lockfile
# would differ when generated on MacOS, so we generate the lockfile
# on a linux image.
if [[ "$(uname)" == 'Linux' ]]; then
    # `pip install pip-tools` on dataproc by default installs into the
    # user's local bin which is not on the PATH
    PATH="$PATH:$HOME/.local/bin" $command
else
	docker run --rm -it \
        -v $(pwd):/hail \
		python:3.9-slim \
		/bin/bash -c "pip install 'pip-tools==6.13.0' && cd /hail && $command"
fi
