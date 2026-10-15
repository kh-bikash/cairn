# Deploying Cairn

One small VM runs the whole backend: FalkorDB, the API and Caddy (automatic HTTPS, also serves the web app).
The web app is additionally deployed on Vercel, which forwards `/api/*` to the VM.

```
browser ──► Vercel (web) ──/api/*──► Caddy :443 ──► api :8000 ──► falkordb :6379 (internal only)
```

## What runs where

| Piece | Where | Notes |
|---|---|---|
| `deploy/aws/provision.sh` | EC2 user-data, once | swap, Docker, Compose, automatic security updates |
| `deploy/aws/deploy.sh` | on the VM, via SSM | pulls a release from S3, health-gated rollout |
| `deploy/docker-compose.prod.yml` | on the VM | pinned images, memory limits, log rotation, data in `/opt/cairn-data` |
| `deploy/Caddyfile` | on the VM | HTTPS, security headers, unbuffered SSE |
| `web/vercel.json` | Vercel | rewrites `/api/*` to the VM; the Vercel project's root directory is `web`, and every push to `main` deploys it |

The VM has no SSH and no open database port: the security group allows only 80/443, and administration goes
through AWS Systems Manager. Its IAM role can read only `release/*` in the deploy bucket.

## AWS resources (ap-south-1)

| Resource | Name |
|---|---|
| EC2 instance | `cairn` (t3.small, 20 GB gp3 encrypted, IMDSv2, standard CPU credits) |
| Elastic IP | tagged `project=cairn`; the HTTPS host is `<ip-with-dashes>.sslip.io` |
| Security group | `cairn-web` |
| IAM role / instance profile | `cairn-ec2` |
| S3 bucket | `cairn-deploy-<account-id>` (private, encrypted, versioned) |

## Release layout in S3

```
release/cairn-release.tar.gz   git archive of the repo + prebuilt web/dist
release/cairn.env              runtime configuration and secrets (never in git)
release/deploy.sh              this rollout script
release/dump.rdb               graph data (restored on first deploy, or with --reseed)
release/llm-cache.tar.gz       cached model responses
```

## Updating

```bash
# build the release
git archive --format=tar HEAD | tar -x -C /tmp/rel && (cd web && npm run build) && cp -r web/dist /tmp/rel/web/
tar -czf cairn-release.tar.gz -C /tmp/rel .
aws s3 cp cairn-release.tar.gz s3://$BUCKET/release/

# roll it out (add --reseed to replace the server's graph with release/dump.rdb)
aws ssm send-command --instance-ids $INSTANCE --document-name AWS-RunShellScript \
  --parameters 'commands=["aws s3 cp s3://'$BUCKET'/release/deploy.sh /usr/local/bin/cairn-deploy","chmod 755 /usr/local/bin/cairn-deploy","/usr/local/bin/cairn-deploy '$BUCKET'"]'
```

To ship a fresh graph: `docker exec cairn-falkordb-1 redis-cli SAVE`, copy `/var/lib/falkordb/data/dump.rdb`
out of the local container, upload it to `release/dump.rdb`, and deploy with `--reseed`.
Without a `dump.rdb`, the API builds both demo workspaces itself on first start (`cairn bootstrap`).

## Tearing down

Terminate the instance, release the Elastic IP, then delete the security group, the `cairn-ec2` instance
profile and role, and the bucket. Nothing else is billed.
