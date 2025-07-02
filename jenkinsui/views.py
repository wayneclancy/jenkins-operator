from django.shortcuts import render, redirect
from kubernetes import client, config
from datetime import datetime
import os
from django.views.decorators.csrf import csrf_exempt
from django.urls import path

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

def home(request):
    jenkins_instances = v1.list_namespaced_custom_object(
        group=CRD_GROUP,
        version=CRD_VERSION,
        namespace=NAMESPACE,
        plural=CRD_PLURAL
    )
    return render(request, "home.html", {"instances": jenkins_instances.get("items", [])})

def delete_jenkins(request, name):
    v1.delete_namespaced_custom_object(
        group=CRD_GROUP,
        version=CRD_VERSION,
        namespace=NAMESPACE,
        plural=CRD_PLURAL,
        name=name
    )
    #Need to add removal of configmaps/secrets/ingress

    return redirect("/")

def restart_jenkins(request, name):
    patch = {
        "metadata": {
            "annotations": {
                "kubectl.kubernetes.io/restartedAt": datetime.utcnow().isoformat() + "Z"
            }
        }
    }
    v1.patch_namespaced_custom_object(
        group=CRD_GROUP,
        version=CRD_VERSION,
        namespace=NAMESPACE,
        plural=CRD_PLURAL,
        name=name,
        body=patch
    )
    return redirect("/")

@csrf_exempt
def create_jenkins(request):
    if request.method == "POST":
        name = request.POST.get("name", "example")
        image = request.POST.get("image", "jenkins/jenkins:2.401.1-lts")
        jcasc = request.POST.get("jcasc", "")
        cluster = request.POST.get("cluster", "/mycluster").lstrip("/")

        s_name = f"{name}-jcasc-secret"
        secret_name = f"{name}-jcasc"
        configmap_name = f"{name}-groovy-configmap"

        # Create Secret for jcasc.yaml
        corev1.create_namespaced_config_map(
            namespace=NAMESPACE,
            body=client.V1ConfigMap(
                api_version="v1",
                kind="ConfigMap",
                metadata=client.V1ObjectMeta(name=secret_name),
                data={"jcasc.yaml": jcasc}
            )
        )
        corev1.create_namespaced_secret(
            namespace=NAMESPACE,
            body=client.V1Secret(
                api_version="v1",
                kind="Secret",
                metadata=client.V1ObjectMeta(name=s_name),
                type="Opaque",
                string_data={"jcasc.yaml": jcasc}
            )
        )

        # Create ConfigMap for init.groovy
        corev1.create_namespaced_config_map(
            namespace=NAMESPACE,
            body=client.V1ConfigMap(
                api_version="v1",
                kind="ConfigMap",
                metadata=client.V1ObjectMeta(name=configmap_name),
                data={"init.groovy": 'println("Groovy Init from ConfigMap")'}
            )
        )

        # Jenkins CR
        body = {
            "apiVersion": "jenkins.io/v1alpha2",
            "kind": "Jenkins",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "spec": {
                "configurationAsCode": {
                    "secret": {
                        "name": s_name
                    },
                    "configurations": [
                        {"name": configmap_name}
                    ]
                },
                "groovyScripts": {
                    "configMap": {
                        "name": configmap_name
                    },
                    "secret": {
                        "name": s_name
                    },
                    "configurations": [
                        {"name": configmap_name}
                    ]
                },
                "jenkinsAPISettings": {
                    "authorizationStrategy": "createUser"
                },
                "master": {
             "basePlugins": [
                {"name": "kubernetes", "version": ""},
                {"name": "workflow-job", "version": ""},  # leave blank to auto-resolve latest
                {"name": "workflow-aggregator", "version": ""},
                {"name": "git", "version": ""},
                {"name": "job-dsl", "version": ""},
                {"name": "configuration-as-code", "version": ""},
                {"name": "kubernetes-credentials-provider", "version": ""}
                      ],
                    "disableCSRFProtection": False,
                    "containers": [
                        {
                            "name": "jenkins-master",
                            "image": image,
                            "imagePullPolicy": "Always",
                            "livenessProbe": {
                                "failureThreshold": 12,
                                "httpGet": {"path": "/login", "port": "http", "scheme": "HTTP"},
                                "initialDelaySeconds": 100,
                                "periodSeconds": 10,
                                "successThreshold": 1,
                                "timeoutSeconds": 5
                            },
                            "env": [
                             {
                                "name": "CASC_JENKINS_CONFIG",
                                "value": "/var/jenkins/configuration-as-code-secrets/jcasc.yaml"
                              }
                             ], 
                            "readinessProbe": {
                                "failureThreshold": 10,
                                "httpGet": {"path": "/login", "port": "http", "scheme": "HTTP"},
                                "initialDelaySeconds": 80,
                                "periodSeconds": 10,
                                "successThreshold": 1,
                                "timeoutSeconds": 1
                            },
                            "resources": {
                                "limits": {"cpu": "1500m", "memory": "3Gi"},
                                "requests": {"cpu": "1", "memory": "500Mi"}
                            }
                        }
                    ]
                }
            }
        }

        # Create the Jenkins CR
        v1.create_namespaced_custom_object(
            group=CRD_GROUP,
            version=CRD_VERSION,
            namespace=NAMESPACE,
            plural=CRD_PLURAL,
            body=body
        )

        # Create Ingress
        ingress_name = f"{name}-ingress"
        ingress_body = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": ingress_name,
                "namespace": NAMESPACE
            },
            "spec": {
                "ingressClassName": "nginx",
                "rules": [
                    {
                        "host": os.getenv("DOMAIN", "example.com"),
                        "http": {
                            "paths": [
                                {
                                    "path": f"/{cluster}",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": name,
                                            "port": {"number": 8080}
                                        }
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }

        try:
            networking = client.NetworkingV1Api()
            networking.create_namespaced_ingress(namespace=NAMESPACE, body=ingress_body)
        except Exception as e:
            print(f" Failed to create ingress: {e}")

        return redirect("/")

    return render(request, "create.html")
