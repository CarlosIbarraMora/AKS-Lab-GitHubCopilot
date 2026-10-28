# AKS Workload Identity and CSI Handoff

This handoff documents the Lab 05 identity and secret projection checks for the ZavaShop orchestrator on AKS.

## Low-cost edition

Each section below keeps the original Azure commands and adds a **Track A (k3d, $0)** and **Track B (budget Azure)** note. The Helm chart switches between both worlds with two values:

| Value | Azure (original / Track B) | Track A (k3d) |
|---|---|---|
| `workloadIdentity.enabled` | `true` | `false` |
| `secrets.mode` | `csi` (Key Vault → `SecretProviderClass`) | `k8s` (plain Kubernetes Secret `github-token`) |

Track A uses `infra/aks/helm/zavashop/values-local.yaml`, which already sets both. Run `helm lint` / `helm template` with both value sets after any chart change.

## 1. Re-federate the UAMI

Lab 01 creates the UAMI and AKS OIDC issuer. Re-create or verify the federated credential for the orchestrator service account:

```bash
source .env.lab

az identity federated-credential create \
  --name aks-orchestrator \
  --identity-name "$UAMI" \
  --resource-group "$RG" \
  --issuer "$AKS_OIDC" \
  --subject system:serviceaccount:zavashop:orchestrator-sa \
  --audience api://AzureADTokenExchange
```

- **Track A:** skip. k3d has no Entra ID OIDC federation; the chart renders no `azure.workload.identity/*` label/annotation and no `AZURE_CLIENT_ID` when `workloadIdentity.enabled=false`.
- **Track B:** unchanged (Workload Identity is free).

## 2. Install Secrets Store CSI Driver

Enable the AKS add-on or install the driver plus Azure provider according to your cluster baseline:

```bash
az aks enable-addons \
  -g "$RG" -n "$AKS" \
  --addons azure-keyvault-secrets-provider

kubectl get pods -n kube-system -l app=secrets-store-csi-driver
```

- **Track A:** skip. With `secrets.mode=k8s` the chart renders no `SecretProviderClass` and no CSI volume.
- **Track B:** the add-on is enabled at cluster creation (`--enable-addons azure-keyvault-secrets-provider` in Lab 01 step 4); just run the `kubectl get pods` check.

## 3. Seed `GITHUB-TOKEN` in Key Vault

For the lab, seed a GitHub Copilot-compatible token manually. Do not check it into the repo.

```bash
az keyvault secret set \
  --vault-name "$KV" \
  --name GITHUB-TOKEN \
  --value "$GITHUB_TOKEN"
```

The Helm chart projects this as Kubernetes secret `github-token` through `SecretProviderClass`, then maps it to the container as `GITHUB_TOKEN`.

- **Track A:** create the same secret name directly in both namespaces (the orchestrator and the 8 "ACA" workloads read it via `secretKeyRef`). Never commit the token; unset it afterwards.

  ```bash
  for ns in zavashop zavashop-aca; do
    kubectl -n $ns create secret generic github-token \
      --from-literal=token="$GITHUB_TOKEN" --dry-run=client -o yaml | kubectl apply -f -
  done
  kubectl -n zavashop get secret github-token -o jsonpath='{.data.token}' | wc -c   # > 0, never print the value
  unset GITHUB_TOKEN
  ```

- **Track B:** keep Key Vault (cents per month) as written above.

## 4. Validate Entra ID and Azure RBAC

Human operators should authenticate through Microsoft Entra ID and Azure RBAC, not local admin kubeconfig.

```bash
az aks show -g "$RG" -n "$AKS" --query aadProfile -o yaml

az role assignment list --scope "$AKS_ID" \
  --query "[].{role:roleDefinitionName, principal:principalName}" \
  -o table
```

Do not request local admin kubeconfig credentials in CI.

- **Track A:** no Entra ID. k3d writes a local admin kubeconfig for the developer only; confirm the context and that it never reaches CI:

  ```bash
  kubectl config current-context        # k3d-zavashop
  kubectl get ns zavashop zavashop-aca
  ```

- **Track B:** unchanged. Keep `--enable-aad --enable-azure-rbac` with the school's existing AKS admin group, and sign in with `kubelogin convert-kubeconfig -l azurecli`.

## 5. Validate Defender, Policy, and Monitoring

```bash
az aks show -g "$RG" -n "$AKS" \
  --query addonProfiles.azurepolicy.enabled -o tsv

az aks show -g "$RG" -n "$AKS" \
  --query securityProfile.defender.securityMonitoring.enabled -o tsv

az security pricing show -n Containers --query pricingTier -o tsv
az security pricing show -n KeyVaults --query pricingTier -o tsv

az aks show -g "$RG" -n "$AKS" \
  --query addonProfiles.omsagent.config.logAnalyticsWorkspaceResourceID -o tsv
```

- **Track A and Track B (`ZAVA_LOWCOST=1`):** Defender plans, the Azure Policy add-on and Container Insights are skipped. Replace them with free open-source equivalents:

  ```bash
  # Policy guardrails (replaces the Azure Policy add-on) — Track A
  helm repo add kyverno https://kyverno.github.io/kyverno/
  helm upgrade --install kyverno kyverno/kyverno -n kyverno --create-namespace
  kubectl -n kyverno get deploy                       # all available

  # Image findings (replaces Defender for Containers) — both tracks; CI runs the same gate
  for svc in orchestrator inventory supplier logistics pricing \
             inventory-mcp supplier-mcp shipping-mcp pricing-mcp; do
    trivy image --severity CRITICAL,HIGH --exit-code 1 "localhost:5001/zavashop/$svc:$GIT_SHA"
  done
  ```

  Track B should also confirm the Defender plans stayed on the free tier, so nothing bills per core:

  ```bash
  az security pricing list --query "[].{name:name,tier:pricingTier}" -o table
  ```

## 6. Production GitHub App Token Broker Stub

The lab uses a Key Vault-hosted `GITHUB-TOKEN`. For production, replace it with a GitHub App plus OIDC-backed token broker that issues short-lived Copilot SDK credentials to the orchestrator pod. Keep the same CSI and Workload Identity pattern, but rotate the credential source from static secret to broker endpoint.
