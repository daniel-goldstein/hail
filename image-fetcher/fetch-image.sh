#!/bin/sh
set -e

gcloud -q auth activate-service-account \
  --key-file=/secrets/gcr-pull.json

gcloud -q auth configure-docker gcr.io

DEFAULT_NAMESPACE=$(jq -r '.default_namespace' < /deploy-config/deploy-config.json)
case $DEFAULT_NAMESPACE in
    default)
	NOTEBOOK_BASE_PATH=''
	;;
    *)
	NOTEBOOK_BASE_PATH="/$DEFAULT_NAMESPACE/notebook"
	;;
esac

ln -s /ssl-config/ssl-config.curlrc "$HOME/.curlrc"

echo "Namespace: $DEFAULT_NAMESPACE; Home: $HOME"
while true; do
    if curl -sSL "https://notebook$NOTEBOOK_BASE_PATH/images" > image-fetch-output.log 2>&1;
    then
        for image in "gcr.io/$PROJECT/base:latest" \
                         gcr.io/google.com/cloudsdktool/cloud-sdk:310.0.0-alpine \
                         $(cat image-fetch-output.log); do
            docker pull "$image" || true
        done
        sleep 360
    else
        1>&2 cat image-fetch-output.log
    fi
done
