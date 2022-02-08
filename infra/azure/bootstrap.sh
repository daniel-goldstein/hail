#!/bin/bash

source ../bootstrap_utils.sh

setup_az() {
    curl --connect-timeout 5 \
         --max-time 10 \
         --retry 5 \
         --retry-all-errors \
         --retry-max-time 40 \
         --location \
         --fail \
         --silent \
         --show-error \
         https://aka.ms/InstallAzureCLIDeb | sudo bash
    az login --identity
}

create_terraform_remote_storage() {
    RESOURCE_GROUP=${RESOURCE_GROUP:-$1}
    STORAGE_CONTAINER_NAME=${STORAGE_CONTAINER_NAME:-"tfstate"}

    # NOTE: This name is assumed to be globally unique
    # NOTE: must be 3-24 lowercase letters and numbers only possibly_invalid_storage_account_name="$(cat /dev/urandom | LC_ALL=C tr -dc '0-9' | head -c 4)${RESOURCE_GROUP}"
    STORAGE_ACCOUNT_NAME=$(LC_ALL=C tr -dc 'a-z0-9' <<< "${possibly_invalid_storage_account_name}" | head -c 24)

    # This storage account/container will be created if it does not already exist
    az storage account create -n $STORAGE_ACCOUNT_NAME -g $RESOURCE_GROUP
    STORAGE_ACCOUNT_KEY=$(az storage account keys list \
        -g $RESOURCE_GROUP \
        --account-name $STORAGE_ACCOUNT_NAME \
        | jq -rj '.[0].value'
    )
    az storage container create -n $STORAGE_CONTAINER_NAME \
        --account-name $STORAGE_ACCOUNT_NAME \
        --account-key $STORAGE_ACCOUNT_KEY
    cat >remote_storage.tfvars <<EOF
storage_account_name = "$STORAGE_ACCOUNT_NAME"
container_name       = "$STORAGE_CONTAINER_NAME"
key                  = "haildev.tfstate"
EOF
}

init_terraform() {
    RESOURCE_GROUP=${RESOURCE_GROUP:-$1}
    STORAGE_CONTAINER_NAME=${STORAGE_CONTAINER_NAME:-"tfstate"}

    STORAGE_ACCOUNT_KEY=$(az storage account keys list \
        -g $RESOURCE_GROUP \
        --account-name $STORAGE_ACCOUNT_NAME \
        | jq -rj '.[0].value'
    )
    remote_storage_access_key="$(mktemp)"
    trap 'rm -f -- "$remote_storage_access_key"' EXIT

    cat >$remote_storage_access_key <<EOF
access_key           = "$STORAGE_ACCOUNT_KEY"
EOF

    terraform init -backend-config=$remote_storage_access_key -backend-config=remote_storage.tfvars
}

grant_auth_sp_admin_consent() {
    az ad app permission admin-consent --id "$(terraform output -raw modules.auth.sp_application_id)"
}


full_bootstrap_from_scratch() {
    $RESOURCE_GROUP=$1
    $SUBSCRIPTION_ID=$2
    $REPO=$3
    $BRANCH=$4
    $USERNAME=$5
    $OBJECT_ID=$6

    echo "Creating remote terraform state storage"
    create_terraform_remote_storage $RESOURCE_GROUP
    init_terraform $RESOURCE_GROUP

    echo "Terraforming cloud infrastructure"
    terraform apply -var-file=global.tfvars
    grant_auth_sp_admin_consent

    echo "Register the following IP with your domain"
    terraform output -raw gateway_ip

    echo "Deploying the bootstrap vm"
    # TODO Make this source-able and runnable to get the IP address
    ./create_bootstrap_vm.sh $RESOURCE_GROUP $SUBSCRIPTION_ID
    IP=TODO
    USERNAME=$(az ad signed-in-user show --output tsv --query mailNickname)
    ssh -i ~/.ssh/id_rsa $USERNAME@$IP 'bash -s' <<EOF
# Run on the bootstrap vm
git clone https://github.com/$REPO/hail.git
cd hail/infra
./install_bootstrap_dependencies.sh
EOF

    ssh -i ~/.ssh/id_rsa $USERNAME@$IP 'bash -s' <<EOF
# Run on the bootstrap vm
export HAIL=~/hail
cd hail/infra/azure
./bootstrap.sh full_bootstrap_vm_steps $RESOURCE_GROUP $REPO $USERNAME $OBJECT_ID
EOF
}

full_bootstrap_vm_steps() {
    $RESOURCE_GROUP=$1
    $REPO=$2
    $BRANCH=$3
    $USERNAME=$4
    $OBJECT_ID=$5

    setup_az
    azsetcluster $RESOURCE_GROUP
    deploy_unmanaged
    $HAIL/batch/az-create-worker-image.sh

    download-secret global-config && sudo cp -r contents /global-config && popd
    download-secret database-server-config && sudo cp -r contents /sql-config && popd
    bootstrap $REPO/hail:$BRANCH deploy_batch
    bootstrap $REPO/hail:$BRANCH create_initial_user $USERNAME $OBJECT_ID

    # TODO Think of good way to do subdomains.txt
    make -C $HAIL/gateway deploy
}
$@
