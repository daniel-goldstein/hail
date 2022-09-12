resource "google_artifact_registry_repository" "repository" {
  provider = google-beta
  format = "DOCKER"
  repository_id = var.repository_id
  location = var.region
}

resource "google_artifact_registry_repository_iam_member" "artifact_registry_reader" {
  provider = google-beta

  for_each = var.reader_gsa_emails
  repository = google_artifact_registry_repository.repository.name
  location = var.region
  role = "roles/artifactregistry.reader"
  member = "serviceAccount:${each.value}"
}

resource "google_artifact_registry_repository_iam_member" "artifact_registry_push_admin" {
  provider = google-beta
  project = var.project
  repository = google_artifact_registry_repository.repository.name
  location = var.region
  role = "roles/artifactregistry.admin"
  member = "serviceAccount:${var.admin_gsa_email}"
}
