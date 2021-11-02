variable "github_oauth_token" {}
variable "github_user1_oauth_token" {}
variable "bucket_location" {}
variable "bucket_storage_class" {}
variable "watched_branches" {
  type = list(tuple([string, bool]))
}
variable "ci_email" {}

module "bucket" {
  source        = "../gcs_bucket"
  short_name    = "hail-ci"
  location      = var.bucket_location
  storage_class = var.bucket_storage_class
}

resource "google_storage_bucket_iam_member" "ci_bucket_admin" {
  bucket = module.bucket.name
  role = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.ci_email}"
}

resource "kubernetes_secret" "ci_config" {
  metadata {
    name = "ci-config"
  }

  data = {
    storage_uri = "gs://${module.bucket.name}"
    watched_branches = jsonencode(var.watched_branches)
  }
}

resource "kubernetes_secret" "zulip_config" {
  metadata {
    name = "zulip-config"
  }

  data = {
    ".zuliprc" = file("~/.hail/.zuliprc")
  }
}

resource "kubernetes_secret" "hail_ci_0_1_github_oauth_token" {
  metadata {
    name = "hail-ci-0-1-github-oauth-token"
  }

  data = {
    "oauth-token" = var.github_oauth_token
  }
}

resource "kubernetes_secret" "hail_ci_0_1_service_account_key" {
  metadata {
    name = "hail-ci-0-1-service-account-key"
  }

  data = {
    "user1" = var.github_user1_oauth_token
  }
}
