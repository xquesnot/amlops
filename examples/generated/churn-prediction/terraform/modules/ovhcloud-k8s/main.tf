# Module stub for provider 'ovhcloud'.
# TODO(provider owner): implement a managed Kubernetes cluster, node pools mapped from
# catalogue instance types, and an S3-compatible versioned artefact bucket.
variable "cluster_name" { type = string }
variable "region" { type = string }
variable "k8s_version" { type = string }
variable "node_pools" {
  type = list(object({ name = string, instance_type = string, min = number, max = number }))
}
output "kubeconfig" {
  value     = null
  sensitive = true
}
