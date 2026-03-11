output "cluster_id" {
  value = azurerm_kubernetes_cluster.missioncontrol.id
}

output "kube_admin_config_raw" {
  value     = azurerm_kubernetes_cluster.missioncontrol.kube_admin_config_raw
  sensitive = true
}
