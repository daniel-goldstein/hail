#!/bin/bash

source ../bootstrap_utils.sh

function configure_gcloud() {
    REGION=${1:-"us-central1"}
    ZONE=${2:-"us-central1-a"}

    gcloud -q auth configure-docker
    gcloud -q auth configure-docker $REGION-docker.pkg.dev
    gcloud container clusters get-credentials --zone $ZONE vdc
}

"$@"
