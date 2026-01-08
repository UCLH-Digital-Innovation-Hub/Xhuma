variable "resource_group_name" {
  description = "The name of the resource group"
  type        = string
}

variable "location" {
  description = "The Azure location for resources"
  type        = string
  default     = "UK South"
}

variable "app_service_name" {
  description = "Name of the Azure Web App"
  type        = string
}

variable "redis_name" {
  description = "Name of the Redis Cache instance"
  type        = string
}

variable "redis_sku_name" {
  description = "The SKU of Redis to use. Possible values are Basic, Standard and Premium."
  type        = string
  default     = "Standard"
}

variable "redis_family" {
  description = "The SKU family to use. C = Basic/Standard, P = Premium."
  type        = string
  default     = "C"
}

variable "redis_capacity" {
  description = "The size of the Redis cache to deploy. Valid values for a SKU family of C (Basic/Standard) are 0, 1, 2, 3, 4, 5, 6."
  type        = number
  default     = 1
}

variable "api_key" {
  description = "API Key for Xhuma"
  type        = string
  sensitive   = true
}

variable "jwt_key" {
  description = "JWT Key"
  type        = string
  sensitive   = true
}

variable "registry_id" {
  description = "Registry ID"
  type        = string
}

variable "docker_image" {
  description = "Docker image to deploy"
  type        = string
  default     = "ghcr.io/uclh-digital-innovation-hub/xhuma:latest"
}

variable "docker_registry_url" {
  description = "Docker registry URL"
  type        = string
  default     = "https://ghcr.io"
}

variable "docker_registry_username" {
  description = "Docker registry username"
  type        = string
}

variable "docker_registry_password" {
  description = "Docker registry password"
  type        = string
  sensitive   = true
}
