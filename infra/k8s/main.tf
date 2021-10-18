terraform {
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "2.2.0"
    }
  }
  # TODO Make this work for google too
  backend "azurerm" {}
}

locals {
  docker_prefix = var.global_config.global.docker_prefix
  docker_root_image = "${local.docker_prefix}/ubuntu:18.04"
}

provider "kubernetes" {
  config_path = "~/.kube_config"
}

resource "kubernetes_secret" "container_registry_push_credentials" {
  metadata {
    name = "container-registry-push-credentials"
  }

  data = {
    "config.json" = jsonencode({
      "auths": {
        (local.docker_prefix): {
          "auth": base64encode(
            "${var.registry_push_credentials.username}:${var.registry_push_credentials.password}"
          )
        }
      }
    })
  }
}
