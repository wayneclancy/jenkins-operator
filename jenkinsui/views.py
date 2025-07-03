# Updated views.py with status, detail, and edit support for Jenkins stacks

from django.shortcuts import render, redirect
from kubernetes import client, config
from datetime import datetime
import os
import base64
from django.views.decorators.csrf import csrf_exempt
from django.urls import path
import logging

# Set up basic logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

# Load kube config - in-cluster or from file
def load_kube_config():
    if os.getenv("KUBERNETES_SERVICE_HOST"):
        config.load_incluster_config()
    else:
        config.load_kube_config()

load_kube_config()

v1 = client.CustomObjectsApi()
corev1 = client.CoreV1Api()
networking = client.NetworkingV1Api()
NAMESPACE = os.getenv("JENKINS_NAMESPACE", "jenkins")
CRD_GROUP = "jenkins.io"
CRD_VERSION = "v1alpha2"
CRD_PLURAL = "jenkins"
DEFAULT_DOMAIN = os.getenv("JENKINS_DEFAULT_DOMAIN", "domain.com")

# Show all Jenkins instances and their status
def home(request):
    jenkins_instances = v1.list_namespaced_custom_object(
        group=CRD_GROUP,
        version=CRD_VERSION,
        namespace=NAMESPACE,
        plural=CRD_PLURAL
    ).get("items", [])

    for inst in jenkins_instances:
        name = inst["metadata"]["name"]
        try:
            pod_list = corev1.list_namespaced_pod(
                namespace=NAMESPACE,
                label_selector=f"jenkins-cr={name}"
            )
            pod = pod_list.items[0]
            inst["status"] = pod.status.phase
        except Exception:
            inst["status"] = "Unknown"

    return render(request, "home.html", {"instances": jenkins_instances})

# Create Jenkins instance (re-added)
@csrf_exempt
def create_jenkins(request):
    if request.method == "POST":
        name = request.POST.get("name", "example")
        image = request.POST.get("image", "jenkins/jenkins:lts")
        jcasc = request.POST.get("jcasc", "")
        domain = request.POST.get("domain", DEFAULT_DOMAIN)

        secret_name = f"{name}-jcasc-secret"
        configmap_name = f"{name}-groovy-configmap"

        # Create secret for jcasc.yaml
        corev1.create_namespaced_secret(
            namespace=NAMESPACE,
            body=client.V1Secret(
                metadata=client.V1ObjectMeta(name=secret_name),
                type="Opaque",
                string_data={"jcasc.yaml": jcasc}
            )
        )

        # Create configmap for init.groovy
        corev1.create_namespaced_config_map(
            namespace=NAMESPACE,
            body=client.V1ConfigMap(
                metadata=client.V1ObjectMeta(name=configmap_name),
                data={"init.groovy": 'println("Groovy Init")'}
            )
        )

        # Create Jenkins custom resource
        body = {
            "apiVersion": "jenkins.io/v1alpha2",
            "kind": "Jenkins",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "spec": {
                "configurationAsCode": {
                    "secret": {"name": secret_name},
                    "configurations": [{"name": configmap_name}]
                },
                "groovyScripts": {
                    "secret": {"name": secret_name},
                    "configurations": [{"name": configmap_name}]
                },
                "jenkinsAPISettings": {
                    "authorizationStrategy": "createUser"
                },
                "master": {
                    "disableCSRFProtection": False,
                    "basePlugins": [
                        {"name": "configuration-as-code", "version": "1963.v24e046127a_3f"},
                        {"name": "kubernetes", "version": ""},
                        {"name": "workflow-job", "version": ""},
                        {"name": "workflow-aggregator", "version": ""},
                        {"name": "git", "version": ""},
                        {"name": "job-dsl", "version": ""},
                        {"name": "kubernetes-credentials-provider", "version": ""}
                    ],
                    "containers": [
                        {
                            "name": "jenkins-master",
                            "image": image,
                            "imagePullPolicy": "Always",
                            "env": [
                                {
                                    "name": "CASC_JENKINS_CONFIG",
                                    "value": "/var/jenkins/configuration-as-code-secrets/jcasc.yaml",
                                     
                                },
                                {
                                     "name": "JENKINS_OPTS",
                                     "value": f"--prefix=/{name}"
                                }
                            ],
                            "resources": {
                                "limits": {
                                    "cpu": "1500m",
                                    "memory": "2Gi"
                                },
                                "requests": {
                                    "cpu": "500m",
                                    "memory": "1Gi"
                                }
                            }
                        }
                    ]
                }
            }
        }

        v1.create_namespaced_custom_object(
            group=CRD_GROUP,
            version=CRD_VERSION,
            namespace=NAMESPACE,
            plural=CRD_PLURAL,
            body=body
        )


        # Ingress creation block (no try block)
        service_name = f"jenkins-operator-http-{name}"
        ingress = client.V1Ingress(
            api_version="networking.k8s.io/v1",
            kind="Ingress",
            metadata=client.V1ObjectMeta(
                name=f"{name}-ingress",
                namespace=NAMESPACE,
                labels={"jenkins-cr": name},
                annotations={
                    "nginx.ingress.kubernetes.io/rewrite-target": "/"
                }
            ),
            spec=client.V1IngressSpec(
                ingress_class_name="nginx",
                rules=[
                    client.V1IngressRule(
                        host=domain,
                        http=client.V1HTTPIngressRuleValue(
                            paths=[
                                client.V1HTTPIngressPath(
                                    path=f"/{name}",
                                    path_type="Prefix",
                                    backend=client.V1IngressBackend(
                                        service=client.V1IngressServiceBackend(
                                            name=service_name,
                                            port=client.V1ServiceBackendPort(number=8080)
                                        )
                                    )
                                )
                            ]
                        )
                    )
                ]
            )
        )
        networking.create_namespaced_ingress(namespace=NAMESPACE, body=ingress)
        logger.info(f"Ingress {name}-ingress created for host {domain} on path /{name}")

        return redirect("/")
    return render(request, "create.html")

# View Jenkins instance details
def view_jenkins(request, name):
    instance = v1.get_namespaced_custom_object(
        group=CRD_GROUP,
        version=CRD_VERSION,
        namespace=NAMESPACE,
        plural=CRD_PLURAL,
        name=name
    )
    return render(request, "view.html", {"instance": instance})

# Edit jcasc.yaml
def edit_jenkins(request, name):
    s_name = f"{name}-jcasc-secret"

    if request.method == "POST":
        updated_jcasc = request.POST.get("jcasc")
        corev1.patch_namespaced_secret(
            name=s_name,
            namespace=NAMESPACE,
            body={"stringData": {"jcasc.yaml": updated_jcasc}}
        )
        return redirect("/")
    else:
        secret = corev1.read_namespaced_secret(s_name, NAMESPACE)
        encoded = secret.data["jcasc.yaml"]
        decoded = base64.b64decode(encoded).decode("utf-8")
        return render(request, "edit.html", {"name": name, "jcasc": decoded})

# Delete Jenkins instance (with future cleanup placeholder)
def delete_jenkins(request, name):
    v1.delete_namespaced_custom_object(
        group=CRD_GROUP,
        version=CRD_VERSION,
        namespace=NAMESPACE,
        plural=CRD_PLURAL,
        name=name
    )

    # Clean up ConfigMaps
    configmaps = corev1.list_namespaced_config_map(
        namespace=NAMESPACE,
        label_selector=f"jenkins-cr={name}"
    ).items
    filtered_configmaps = [s for s in configmaps if not s.metadata.name.startswith("jenkins-operator")]
    for cm in filtered_configmaps:
        corev1.delete_namespaced_config_map(name=cm.metadata.name, namespace=NAMESPACE)

    # Clean up Secrets (filtering unwanted ones)
    secrets = corev1.list_namespaced_secret(
        namespace=NAMESPACE,
        label_selector=f"jenkins-cr={name}"
    ).items
    filtered_secrets = [s for s in secrets if not s.metadata.name.startswith("jenkins-operator")]
    for secret in filtered_secrets:
        corev1.delete_namespaced_secret(name=secret.metadata.name, namespace=NAMESPACE)

    # Clean up Ingress
    try:
        networking.delete_namespaced_ingress(name=f"{name}-ingress", namespace=NAMESPACE)
    except Exception as e:
        logger.warning(f"Ingress deletion failed or not found: {e}")

    return redirect("/")


def find_service_by_label(name):
    try:
        services = corev1.list_namespaced_service(
            namespace=NAMESPACE,
            label_selector=f"jenkins-cr={name}"
        )
        if services.items:
            return services.items[0].metadata.name
        else:
            logger.warning(f"No service found with label jenkins-cr={name}")
            return None
    except Exception as e:
        logger.error(f"Error fetching service for {name}: {e}")
        return None

def restart_jenkins(request, name):
    logger.debug(f"Attempting restart of Jenkins instance: {name}")

    # Patch the CR with restart annotation (optional)
    patch = {
        "metadata": {
            "annotations": {
                "kubectl.kubernetes.io/restartedAt": datetime.utcnow().isoformat() + "Z"
            }
        }
    }

    try:
        v1.patch_namespaced_custom_object(
            group=CRD_GROUP,
            version=CRD_VERSION,
            namespace=NAMESPACE,
            plural=CRD_PLURAL,
            name=name,
            body=patch
        )
        logger.debug(f"Restart annotation applied to Jenkins {name}.")
    except Exception as e:
        logger.error(f"Failed to patch Jenkins for restart: {e}")

    # Attempt to delete the pod (forces restart)
    try:
        pod_list = corev1.list_namespaced_pod(
            namespace=NAMESPACE,
            label_selector=f"jenkins-cr={name}"
        )
        for pod in pod_list.items:
            pod_name = pod.metadata.name
            corev1.delete_namespaced_pod(name=pod_name, namespace=NAMESPACE)
            logger.debug(f"Deleted pod {pod_name} to force restart.")
    except Exception as e:
        logger.error(f"Failed to delete pod for Jenkins {name}: {e}")

    return redirect("/")
