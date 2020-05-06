from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import json

class Controller(BaseHTTPRequestHandler):
    def sync(self, parent, children):
        # HACK: Currently using serving.kubeflow.org/inferenceservice to identify
        # kubeflow user namespaces.
        # TODO: let Kubeflow profile controller add a pipeline specific label to
        # user namespaces and use that label instead.
        pipeline_enabled = parent.get("metadata", {}).get("labels", {}).get("serving.kubeflow.org/inferenceservice")

        if not pipeline_enabled:
            return {"status": {}, "children": []}

        # Compute status based on observed state.
        desired_status = {
            "kubeflow-pipelines-config-ready": \
                len(children["Secret.v1"]) == 1 and \
                len(children["ConfigMap.v1"]) == 1 and \
                len(children["Deployment.apps/v1"]) == 2 and \
                len(children["Service.v1"]) == 2 and \
                len(children["DestinationRule.networking.istio.io/v1alpha3"]) == 1 and \
                len(children["ServiceRole.rbac.istio.io/v1alpha1"]) == 1 and \
                len(children["ServiceRoleBinding.rbac.istio.io/v1alpha1"]) == 1 and \
                "True" or "False"
        }

        # Generate the desired child object(s).
        # parent is a namespace
        namespace = parent.get("metadata", {}).get("name")
        desired_resources = [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "mlpipeline-minio-artifact",
                "namespace": namespace,
            },
            "data": {
                "accesskey": "bWluaW8=", # base64 for minio
                "secretkey": "bWluaW8xMjM=", # base64 for minio123
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "metadata-grpc-configmap",
                "namespace": namespace,
            },
            "data": {
                "METADATA_GRPC_SERVICE_HOST": "metadata-grpc-service.kubeflow",
                "METADATA_GRPC_SERVICE_PORT": "8080",
            },
        },
        # Visualization server related manifests below
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "labels": {"app": "ml-pipeline-visualizationserver"},
                "name": "ml-pipeline-visualizationserver",
                "namespace": namespace,
            },
            "spec": {
                "selector": {
                    "matchLabels": {"app": "ml-pipeline-visualizationserver"},
                },
                "template": {
                    "metadata": {
                        "labels": {"app": "ml-pipeline-visualizationserver"},
                    },
                    "spec": {
                        "containers": [{
                            "image": "gcr.io/ml-pipeline/visualization-server:0.5.0",
                            "imagePullPolicy": "IfNotPresent",
                            "name": "ml-pipeline-visualizationserver",
                            "ports": [{"containerPort": 8888}],
                        }],
                        "serviceAccountName": "default-editor",
                    },
                },
            },
        },
        {
            "apiVersion": "networking.istio.io/v1alpha3",
            "kind": "DestinationRule",
            "metadata": {
                "name": "ml-pipeline-visualizationserver",
                "namespace": namespace,
            },
            "spec": {
                "host": "ml-pipeline-visualizationserver",
                "trafficPolicy": { "tls": { "mode": "ISTIO_MUTUAL" } }
            }
        },
        {
            "apiVersion": "rbac.istio.io/v1alpha1",
            "kind": "ServiceRole",
            "metadata": {
                "name": "ml-pipeline-visualizationserver",
                "namespace": namespace,
            },
            "spec": {
                "rules": [{ "services": ["ml-pipeline-visualizationserver.*"] }]
            }
        },
        {
            "apiVersion": "rbac.istio.io/v1alpha1",
            "kind": "ServiceRoleBinding",
            "metadata": {
                "name": "ml-pipeline-visualizationserver",
                "namespace": namespace,
            },
            "spec": {
                "subjects": [
                    { "properties": { "source.principal": "cluster.local/ns/kubeflow/sa/ml-pipeline" } }
                ],
                "roleRef": {
                    "kind": "ServiceRole",
                    "name": "ml-pipeline-visualizationserver"
                }
            }
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": "ml-pipeline-visualizationserver",
                "namespace": namespace,
            },
            "spec": {
                "ports": [{
                    "name": "http",
                    "port": 8888,
                    "protocol": "TCP",
                    "targetPort": 8888,
                }],
                "selector": {
                    "app": "ml-pipeline-visualizationserver",
                },
            },
        },
        # Artifact fetcher related resources below.
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "labels": { "app": "ml-pipeline-ui-artifact" },
                "name": "ml-pipeline-ui-artifact",
                "namespace": namespace,
            },
            "spec": {
                "selector": {
                    "matchLabels": { "app": "ml-pipeline-ui-artifact" }
                },
                "template": {
                    "metadata": {
                        "labels": { "app": "ml-pipeline-ui-artifact" },
                    },
                    "spec": {
                        "containers": [
                            {
                                "name": "ml-pipeline-ui-artifact",
                                "image": "gcr.io/gongyuan-pipeline-test/dev/frontend@sha256:ee2fb833105b10ff866b78169b6c56884cafdcf8bc57d684ad2fc7cda757afb6",
                                "imagePullPolicy": "IfNotPresent",
                                "ports": [
                                    { "containerPort": 3000 }
                                ]
                            }
                        ],
                        "serviceAccountName": "default-editor"
                    }
                }
            }
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": "ml-pipeline-ui-artifact",
                "namespace": namespace,
                "labels": { "app": "ml-pipeline-ui-artifact" }
            },
            "spec": {
                "ports": [{
                    "name": "http", # name is required to let istio understand request protocol
                    "port": 80,
                    "protocol": "TCP",
                    "targetPort": 3000
                }],
                "selector": { "app": "ml-pipeline-ui-artifact" }
            }
        },
        ]
        print('Received request', parent, desired_resources)

        return {"status": desired_status, "children": desired_resources}

    def do_POST(self):
        # Serve the sync() function as a JSON webhook.
        observed = json.loads(self.rfile.read(int(self.headers.getheader("content-length"))))
        desired = self.sync(observed["parent"], observed["children"])

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(desired))

HTTPServer(("", 80), Controller).serve_forever()
