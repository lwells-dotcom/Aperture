# Atlas Deploy Runbook

Last verified: 2026-04-19

Atlas runs as a Flask/Gunicorn app with Postgres 16. Two deployment paths exist: Kind (production) and docker-compose (local dev). Both use the same Dockerfile and schema.

---

## Prerequisites

All paths need these set in `.env` (or passed via Helm `--set`):

```
DEMO_TOKEN_SECRET=<hex string, e.g. openssl rand -hex 32>
ANTHROPIC_API_KEY=sk-ant-...
DB_PASSWORD=<something not "atlas" in prod>
```

Optional:
```
OPENAI_API_KEY=sk-...           # fallback LLM
NETBOX_API_TOKEN=...            # only if NetBox streaming endpoints used
GOOGLE_SA_KEY_PATH=...          # only if Google Sheets fetcher used
```

---

## Path 1: Kind + Helm (Production)

This is the prod deploy path. Atlas runs in a Kind cluster with Helm managing the release.

### First-time setup

```bash
# Create Kind cluster (if not already running)
kind create cluster --name atlas

# Build the Docker image
cd DCT_Scripts/Optic_Count
docker build -t atlas-web:1.0.0 .

# Load image into Kind (Kind has its own image store, separate from Docker Desktop)
kind load docker-image atlas-web:1.0.0 --name atlas

# Create a values override for secrets (DO NOT commit this file)
cat > helm/atlas/values-local.yaml << 'EOF'
secrets:
  dbPassword: "your-real-password"
  demoTokenSecret: "a]real-hex-string-not-a-shell-command"
  demoVerifyPin: "123456"
  anthropicApiKey: "sk-ant-..."
EOF

# Install the release
helm install atlas helm/atlas/ -f helm/atlas/values-local.yaml
```

### Redeploy after code changes

This is the one you'll run most often. Any time you change Python code, templates, or dependencies:

```bash
cd DCT_Scripts/Optic_Count

# 1. Rebuild the image
docker build -t atlas-web:1.0.0 .

# 2. Load into Kind (required every time -- Kind nodes don't see Docker Desktop images)
kind load docker-image atlas-web:1.0.0 --name atlas

# 3. Restart the web pods to pick up the new image
kubectl rollout restart deployment/atlas-web

# 4. Wait for rollout
kubectl rollout status deployment/atlas-web

# 5. Re-establish port-forward (rollout restart kills existing forwards)
kubectl port-forward svc/atlas-web 5050:5050
```

If you changed Helm values (secrets, resource limits, replica count, etc.):

```bash
helm upgrade atlas helm/atlas/ -f helm/atlas/values-local.yaml
```

### Schema changes

If you modified `atlas_schema.sql`, the schema-init ConfigMap needs updating:

```bash
# Helm upgrade picks up the new schema file automatically
helm upgrade atlas helm/atlas/ -f helm/atlas/values-local.yaml

# If the Postgres pod already has data and you need to apply migrations manually:
kubectl exec -it sts/atlas-postgres -- psql -U atlas -d atlas
# Then paste your ALTER TABLE / CREATE INDEX statements
```

### Useful commands

```bash
# Check pod status
kubectl get pods

# Tail web logs
kubectl logs -f deployment/atlas-web --all-containers

# Tail Postgres logs
kubectl logs -f sts/atlas-postgres

# Shell into Postgres
kubectl exec -it sts/atlas-postgres -- psql -U atlas -d atlas

# Check health endpoint
curl http://localhost:5050/api/health
```

### Known gotchas

1. `values-local.yaml` secrets must be literal strings. Helm does NOT expand `$(openssl rand ...)`.
2. After `kubectl rollout restart`, your port-forward dies. You have to restart it.
3. `kind load docker-image` is required after every `docker build`. Easy to forget.
4. Pod labels use `app.kubernetes.io/name`, not bare `app=atlas-web`. Use pod name directly for `kubectl logs <pod-name>`.

---

## Path 2: docker-compose (Local Dev)

Simpler path for local development and testing. No Kind cluster needed.

```bash
cd DCT_Scripts/Optic_Count

# Make sure .env exists with at minimum:
# DEMO_TOKEN_SECRET, ANTHROPIC_API_KEY

# Start everything
docker compose up --build -d

# Tail logs
docker compose logs -f web

# Stop
docker compose down

# Nuclear reset (wipes Postgres data volume)
docker compose down -v
```

Ports: Flask on `localhost:5050`, Postgres on `localhost:9000` (not 5432, to avoid conflicts).

Schema auto-initializes via the `initdb.d` mount in docker-compose.yml.

### Rebuild after code changes

```bash
docker compose up --build -d
```

That's it. Compose rebuilds the image and restarts the container. No image loading step needed.

---

## Verifying a deploy

After either path, hit these to confirm things are working:

```bash
# Health check
curl http://localhost:5050/api/health

# Should return {"status": "ok", "postgres": true/false}
```

Then open the web UI at `http://localhost:5050`, upload a cutsheet, and ask a question. Check that the response shows `context_source: POSTGRES` in the response payload (visible in browser dev tools Network tab).

---

## Image details

The Dockerfile is a multi-stage build:
- Stage 1 (builder): installs Python deps from requirements.txt
- Stage 2 (runtime): copies deps + app code, runs as non-root `atlas` user
- Gunicorn: 4 workers, 180s timeout (bumped from 60s for large cutsheet uploads)
- Healthcheck: hits `/api/health` every 30s

## Helm chart structure

```
helm/atlas/
  Chart.yaml
  values.yaml          # defaults (Kind-oriented)
  values-local.yaml    # your secrets override (gitignored)
  files/
    atlas_schema.sql   # bundled for schema-init ConfigMap
  templates/
    configmap.yaml           # non-sensitive env vars
    secret.yaml              # API keys, DB password, auth tokens
    schema-configmap.yaml    # schema SQL for Postgres initdb
    postgres-statefulset.yaml
    postgres-service.yaml
    web-deployment.yaml      # 2 replicas, rolling update
    web-service.yaml         # ClusterIP on 5050
    uploads-pvc.yaml
    ingress.yaml             # disabled by default
    NOTES.txt
```
