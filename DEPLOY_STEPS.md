# Atlas Deploy Guide

Last updated: 2026-04-19

## Prerequisites

- Docker Desktop running
- `kind`, `kubectl`, `helm` installed
- Working directory: `~/Atlas/DCT_Scripts/Optic_Count`

## values-local.yaml

Create this file in `Optic_Count/` (do not commit):

```yaml
secrets:
  dbPassword: "atlas-rocks"
  demoTokenSecret: "d32e404142fc0be7d0ef85b05cfa04495ab4cc3887f798c404c1d0a6e1ce4bd0"
  anthropicApiKey: "sk-ant-..."      # your real key
  netboxApiToken: ""                 # fill in if testing Netbox streaming
```

**Important:** `demoTokenSecret` must be a real hex string. Do NOT use `$(openssl rand ...)` here. Helm does not expand shell commands. Generate one with:

```
openssl rand -hex 32
```

Then paste the output as the value.

## Full Clean Deploy (from scratch)

Run these in order. Each step must finish before starting the next.

### 1. Tear down any existing cluster

```
kind delete cluster
```

### 2. Build the Docker image

```
cd ~/Atlas/DCT_Scripts/Optic_Count
docker build -t atlas-web:1.0.0 .
```

### 3. Create the Kind cluster

```
kind create cluster --name kind
```

### 4. Load the image into Kind

```
kind load docker-image atlas-web:1.0.0
```

### 5. Deploy with Helm

```
helm install atlas ./helm/atlas -f values-local.yaml
```

### 6. Wait for pods

```
kubectl get pods -w
```

Wait until all three pods show `Running` (two web pods + one postgres), then Ctrl+C.

### 7. Port forward

```
kubectl port-forward svc/atlas-atlas-web 5050:5050
```

### 8. Open the app

Browse to `http://localhost:5050` and Cmd+Shift+R to hard refresh.

Default PIN: `123456`

## Quick Rebuild (code changes only)

When you change Python code but the cluster is still running:

```
cd ~/Atlas/DCT_Scripts/Optic_Count
docker build -t atlas-web:1.0.0 .
kind load docker-image atlas-web:1.0.0
kubectl rollout restart deployment atlas-atlas-web
```

**Important:** After a rollout restart, the port-forward dies. Restart it:

```
kubectl port-forward svc/atlas-atlas-web 5050:5050
```

Then hard refresh the browser (Cmd+Shift+R).

## Loading cutsheets into Postgres

After the app is running, load data via the CLI inside the pod:

```
kubectl exec -it deploy/atlas-atlas-web -- \
  python atlas_data_loader.py --file /app/uploads/QNC01.xlsx --site QCY

kubectl exec -it deploy/atlas-atlas-web -- \
  python atlas_data_loader.py --file /app/uploads/ELD01.xlsx --site ELD

kubectl exec -it deploy/atlas-atlas-web -- \
  python atlas_data_loader.py --file /app/uploads/ELD02.xlsx --site ELD
```

Verify:

```
kubectl exec -it atlas-atlas-postgres-0 -- \
  psql -U atlas -d atlas -c "SELECT site_code, count(*) FROM devices GROUP BY site_code;"
```

## Useful Debug Commands

Check pod status:
```
kubectl get pods
```

Tail web logs (use actual pod name from get pods):
```
kubectl logs <pod-name> --tail=50 -c web
```

Check health endpoint:
```
curl http://localhost:5050/api/health
```

Describe a failing pod:
```
kubectl describe pod <pod-name>
```

Check for image load issues:
```
kubectl get events --sort-by='.lastTimestamp' | tail -20
```

## Full Teardown

```
helm uninstall atlas
kubectl delete pvc -l app.kubernetes.io/instance=atlas   # wipes the DB
kind delete cluster
```

## Troubleshooting

**`ErrImageNeverPull`**: Image not loaded into Kind. Run `kind load docker-image atlas-web:1.0.0` again.

**`secrets.dbPassword must be set`**: Helm needs the values file. Use `helm install atlas ./helm/atlas -f values-local.yaml`.

**`secrets.demoTokenSecret must be set to a 32+ byte hex string`**: The token secret in values-local.yaml is empty or contains a shell command instead of an actual hex string. Generate one with `openssl rand -hex 32` and paste the output.

**Port-forward dies after rollout restart**: Expected. The old pods get killed and the tunnel drops. Run `kubectl port-forward svc/atlas-atlas-web 5050:5050` again.

**Browser stuck on "Processing..."**: Check pod logs for tracebacks. If logs only show health checks, your port-forward is dead. Restart it.

**No nodes found for cluster "kind"**: Cluster doesn't exist. Run `kind create cluster --name kind` first.

## Still Open

- `GOOGLE_SA_KEY_JSON` wiring (leave empty until key arrives)
- `imagePullPolicy: Never` is Kind-only, change for prod
- No PodDisruptionBudget, NetworkPolicy, or HPA yet
