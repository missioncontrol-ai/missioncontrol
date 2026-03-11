output "cluster_name" {
  value = aws_eks_cluster.missioncontrol.name
}

output "node_group_name" {
  value = aws_eks_node_group.missioncontrol.node_group_name
}
