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
NAMESPACE = os.getenv("JENKINS_NAMESPACE", "jenkins")
CRD_GROUP = "jenkins.io"
CRD_VERSION = "v1alpha2"
CRD_PLURAL = "jenkins"

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
                label_selector=f"app.kubernetes.io/instance={name}"
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
                    "containers": [
                        {
                            "name": "jenkins-master",
                            "image": image,
                            "imagePullPolicy": "Always",
                            "env": [
                                {
                                    "name": "CASC_JENKINS_CONFIG",
                                    "value": "/var/jenkins/configuration-as-code-secrets/jcasc.yaml"
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
    return redirect("/")

def restart_jenkins(request, name):
    logger.debug(f"Attempting restart of Jenkins instance: {name}")
    patch = {
        "metadata": {
            "annotations": {
                "kubectl.kubernetes.io/restartedAt": datetime.utcnow().isoformat() + "Z"
            }
        }
    }
    try:
        response = v1.patch_namespaced_custom_object(
            group=CRD_GROUP,
            version=CRD_VERSION,
            namespace=NAMESPACE,
            plural=CRD_PLURAL,
            name=name,
            body=patch
        )
        logger.debug(f"Restart patch applied successfully: {response}")
    except Exception as e:
        logger.error(f"Failed to patch Jenkins for restart: {e}")
        return render(request, "error.html", {"message": f"Restart failed for {name}: {str(e)}"})
    
    return redirect("/")
