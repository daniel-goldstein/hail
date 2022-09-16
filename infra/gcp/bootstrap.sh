#!/bin/bash

source ../bootstrap_utils.sh

function configure_gcloud() {
    ZONE=${1:-"us-central1-a"}

    REGIONS=$(get_global_config_field batch_gcp_regions)
    for region in $(echo $REGIONS | jq -r '.[]');
    do
        gcloud -q auth configure-docker $region-docker.pkg.dev
    done
    gcloud container clusters get-credentials --zone $ZONE vdc
}

"$@"
