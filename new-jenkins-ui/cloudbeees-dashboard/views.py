import os
from django.shortcuts import render
from django.http import JsonResponse
from kubernetes import client, config

# Load K8s config from environment or local kubeconfig
def load_k8s_config():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

# Get StatefulSets and their pod statuses
def get_statefulset_info(namespace="cloudbees-core"):
    load_k8s_config()
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()

    sts_list = apps_v1.list_namespaced_stateful_set(namespace=namespace)
    statefulsets = []

    for sts in sts_list.items:
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app={sts.metadata.labels.get('app', '')}")
        pod_info = []

        for pod in pods.items:
            pod_info.append({
                "name": pod.metadata.name,
                "status": pod.status.phase,
                "restarts": sum([cs.restart_count for cs in pod.status.container_statuses or []]),
                "node": pod.spec.node_name,
                "uptime": pod.status.start_time
            })

        casc_bundles = []
        try:
            for vol in sts.spec.template.spec.volumes:
                if vol.config_map and 'casc-bundle' in vol.config_map.name:
                    casc_name = vol.config_map.name
                    configmap = v1.read_namespaced_config_map(casc_name, namespace=namespace)
                    casc_bundles.append({"name": casc_name, "data": configmap.data})
        except Exception:
            pass

        statefulsets.append({
            "name": sts.metadata.name,
            "replicas": sts.status.replicas,
            "ready_replicas": sts.status.ready_replicas,
            "pods": pod_info,
            "casc_bundles": casc_bundles,
            "jenkins_url": f"/{sts.metadata.name}/"
        })

    return statefulsets

# View logs of a specific pod
def get_pod_logs(namespace, pod_name):
    load_k8s_config()
    v1 = client.CoreV1Api()
    try:
        log = v1.read_namespaced_pod_log(name=pod_name, namespace=namespace, tail_lines=100)
        return log
    except client.exceptions.ApiException as e:
        return f"Error: {e}"

# Django view for dashboard
def dashboard(request):
    sts_data = get_statefulset_info()
    return render(request, "dashboard.html", {"statefulsets": sts_data})

# Django view for logs
def pod_logs(request, pod_name):
    logs = get_pod_logs("cloudbees-core", pod_name)
    return JsonResponse({"logs": logs})

