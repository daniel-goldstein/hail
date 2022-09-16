#!/bin/bash

set -ex

source $HAIL/devbin/functions.sh

if [ -z "${NAMESPACE}" ]; then
    echo "Must specify a NAMESPACE environment variable"
    exit 1;
fi

CONTEXT="$(cd $1 && pwd)"
DOCKERFILE="$CONTEXT/$2"
REMOTE_IMAGE_NAME=$3
EXTRA_CACHE=$4

WITHOUT_TAG=$(echo $REMOTE_IMAGE_NAME | sed -E 's/(:[^:]+)(@[^@]+)?$//')
MAIN_CACHE=${WITHOUT_TAG}:cache

if [ "${NAMESPACE}" == "default" ]; then
    CACHE_IMAGE_NAME=${MAIN_CACHE}
else
    DEV_CACHE=${WITHOUT_TAG}:cache-${NAMESPACE}
    CACHE_IMAGE_NAME=${DEV_CACHE}
fi

DOCKER_BUILDKIT=1 docker build \
       --file ${DOCKERFILE} \
       --cache-from ${MAIN_CACHE} \
       ${DEV_CACHE:+--cache-from ${DEV_CACHE}} \
       ${EXTRA_CACHE:+--cache-from ${EXTRA_CACHE}} \
       --build-arg BUILDKIT_INLINE_CACHE=1 \
       --tag ${REMOTE_IMAGE_NAME} \
       --tag ${CACHE_IMAGE_NAME} \
       ${CONTEXT}

time DOCKER_BUILDKIT=1 docker push ${REMOTE_IMAGE_NAME}
time DOCKER_BUILDKIT=1 docker push ${CACHE_IMAGE_NAME}

if [[ -n "${DOCKER_PREFIX}" && -n "${MIRROR_IN_REGIONAL_REPOSITORIES}" ]];
then
    MIRROR_REPOSITORIES=$(get_global_config_field batch_container_repositories)
    IMAGE_NAME=${REMOTE_IMAGE_NAME#"${DOCKER_PREFIX}/"}
    for repository in $(echo $MIRROR_REPOSITORIES | jq -r 'values | .[]')
    do
        mirror_image_name="${repository}/${IMAGE_NAME}"
        time DOCKER_BUILDKIT=1 docker tag ${REMOTE_IMAGE_NAME} ${mirror_image_name}
        time DOCKER_BUILDKIT=1 docker push ${mirror_image_name}
    done
fi
