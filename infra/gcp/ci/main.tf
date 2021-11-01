variable "github_oauth_token" {}
variable "github_user1_oauth_token" {}

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
