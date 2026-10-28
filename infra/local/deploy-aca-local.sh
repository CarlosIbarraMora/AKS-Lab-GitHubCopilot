#!/usr/bin/env bash
# Track A (k3d, $0): deploys the 4 specialists + 4 MCP servers into the
# zavashop-aca namespace. Replaces infra/aca/deploy.sh + agent.bicep locally.
# Requires: GIT_SHA set, images pushed to localhost:5001, secret github-token in $NS.
set -euo pipefail

: "${GIT_SHA:?set GIT_SHA to the current git SHA}"
NS=zavashop-aca
REG=${REGISTRY_IN_CLUSTER:-k3d-zavashop-registry:5001}/zavashop
MODEL=${ZAVA_COPILOT_MODEL:-gpt-5.5}
SVC=$NS.svc.cluster.local

deploy() { # name port
  local name=$1 port=$2
  kubectl -n "$NS" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $name
  labels: {app: $name, app.kubernetes.io/part-of: zavashop}
spec:
  replicas: 1
  selector: {matchLabels: {app: $name}}
  template:
    metadata: {labels: {app: $name, app.kubernetes.io/part-of: zavashop}}
    spec:
      securityContext: {runAsNonRoot: true, runAsUser: 10001, runAsGroup: 10001}
      containers:
        - name: $name
          image: $REG/$name:$GIT_SHA
          ports: [{containerPort: $port, name: http}]
          env:
            - {name: ZAVA_COPILOT_MODEL, value: "$MODEL"}
            - {name: ZAVA_COPILOT_TIMEOUT_SECONDS, value: "120"}
            - {name: ZAVA_INVENTORY_MCP_URL, value: "http://inventory-mcp.$SVC:8080/mcp"}
            - {name: ZAVA_SUPPLIER_MCP_URL, value: "http://supplier-mcp.$SVC:8080/mcp"}
            - {name: ZAVA_SHIPPING_MCP_URL, value: "http://shipping-mcp.$SVC:8080/mcp"}
            - {name: ZAVA_PRICING_MCP_URL, value: "http://pricing-mcp.$SVC:8080/mcp"}
            - name: GITHUB_TOKEN
              valueFrom: {secretKeyRef: {name: github-token, key: token}}
          # Copilot client init can take minutes on a laptop; don't let liveness kill it meanwhile.
          startupProbe: {httpGet: {path: /healthz, port: http}, periodSeconds: 5, timeoutSeconds: 3, failureThreshold: 60}
          readinessProbe: {httpGet: {path: /readyz, port: http}, periodSeconds: 5, timeoutSeconds: 3}
          livenessProbe: {httpGet: {path: /healthz, port: http}, periodSeconds: 10, timeoutSeconds: 3}
          resources: {requests: {cpu: 50m, memory: 128Mi}, limits: {memory: 512Mi}}
---
apiVersion: v1
kind: Service
metadata: {name: $name}
spec:
  selector: {app: $name}
  ports: [{port: $port, targetPort: $port}]
EOF
}

for m in inventory-mcp supplier-mcp shipping-mcp pricing-mcp; do deploy "$m" 8080; done
kubectl -n "$NS" rollout status deploy/inventory-mcp deploy/supplier-mcp deploy/shipping-mcp deploy/pricing-mcp --timeout=180s
for a in inventory supplier logistics pricing; do deploy "$a" 8000; done
kubectl -n "$NS" rollout status deploy/inventory deploy/supplier deploy/logistics deploy/pricing --timeout=180s
