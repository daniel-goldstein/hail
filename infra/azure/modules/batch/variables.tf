variable resource_group {
  type = object({
    id       = string
    name     = string
    location = string
  })
}

variable container_registry_id {
  type = string
}

variable log_analytics_workspace_id {
  type = string
}

variable log_analytics_workspace_key {
  type = string
}
