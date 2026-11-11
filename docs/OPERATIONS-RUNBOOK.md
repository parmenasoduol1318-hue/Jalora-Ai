# Jalora Operations Runbook

This runbook covers the current local Docker Compose deployment. It is not a
production certification: the bundled database is SQLite, external provider
sandboxes and multi-worker production load have not been accepted, and
off-site backup retention must be arranged by the operator.

## Secrets and first production startup

- Supply `JALORA_JWT_SECRET` from a secret manager; production rejects the
  built-in development key and values shorter than 32 characters.
- Supply a separate, valid Fernet key as
  `JALORA_CREDENTIALS_ENCRYPTION_KEY`. Production startup rejects a missing,
  malformed, or JWT-identical key. Keep this key backed up separately from the
  database. Losing it makes encrypted login credentials unrecoverable.
- Do not commit `.env`, provider credentials, database snapshots, or backup
  files. Rotate provider credentials through each provider's console and
  invalidate/revoke the old credential there.
- Redis is reachable only over the Compose network; do not add a public host
  port mapping. The application waits for Redis health before startup.

For an existing installation, take a database backup before changing the
credential encryption key. Put the new
`JALORA_CREDENTIALS_ENCRYPTION_KEY` in the deployment environment. If the prior
key was JWT-derived, leave `JALORA_CREDENTIALS_OLD_KEY` unset and keep the
existing JWT secret unchanged until rotation succeeds. If a dedicated prior
key was configured, provide it through `JALORA_CREDENTIALS_OLD_KEY`.

Stop every application worker to prevent credentials being written with the
old key during rotation. Then run the rotation tool once:

```powershell
docker compose stop app
if ($LASTEXITCODE -ne 0) { throw "Could not stop the application." }
docker compose run --rm --no-deps app python /app/scripts/rotate_credential_key.py
if ($LASTEXITCODE -ne 0) { throw "Credential key rotation failed; keep the application stopped." }
docker compose up -d app
if ($LASTEXITCODE -ne 0) { throw "Could not start the application." }
curl.exe --fail http://localhost:8000/api/health
if ($LASTEXITCODE -ne 0) { throw "Application health check failed." }
```

For a JWT-derived prior key, the command above uses the existing JWT secret.
For a dedicated prior key, load `JALORA_CREDENTIALS_OLD_KEY` into the current
PowerShell environment from the secret manager. In the command block above,
replace the rotation command with:

```powershell
docker compose run --rm --no-deps -e JALORA_CREDENTIALS_OLD_KEY app python /app/scripts/rotate_credential_key.py
```

Do not put the key in a script or source control. Only start the service with
the new key after the command succeeds. On failure, keep the application
stopped, restore the previous key configuration (and restore the pre-rotation
database backup if needed) before bringing the old service back. A failed
rotation is atomic: it does not partially update credential rows. Do not
remove the old key or the pre-rotation backup until credentials have been
verified. Rotate the JWT secret separately after key migration; changing it
invalidates existing sessions and must not replace the old derived key during
this rotation.

## SQLite backup and restore

The backup command uses SQLite's online backup API, so committed WAL data is
included in a consistent snapshot. It validates the result and refuses to
overwrite an existing backup:

```powershell
docker compose exec -T app python /app/scripts/sqlite_database.py backup
if ($LASTEXITCODE -ne 0) { throw "SQLite backup failed." }
New-Item -ItemType Directory -Force .\backups
docker compose cp app:/app/data/backups/<backup-filename>.sqlite3 .\backups\
if ($LASTEXITCODE -ne 0) { throw "Could not copy the backup snapshot." }
```

Replace `<backup-filename>` with the exact path printed by the backup command.

Database snapshots contain user data and encrypted credentials. Encrypt them
before copying to off-host storage, restrict access, and define a retention
period appropriate to the deployment. The local script does not implement
off-site storage or automatic expiration.

To restore, stop the application first. The tool validates the input and makes
a validated pre-restore safety copy before replacing the SQLite file:

```powershell
docker compose exec -T app mkdir -p /app/data/backups
if ($LASTEXITCODE -ne 0) { throw "Could not prepare the backup directory." }
docker compose stop app
if ($LASTEXITCODE -ne 0) { throw "Could not stop the application." }
docker compose cp .\backups\jalora-backup.sqlite3 app:/app/data/backups/restore.sqlite3
if ($LASTEXITCODE -ne 0) { throw "Could not copy the restore snapshot." }
docker compose run --rm --no-deps app python /app/scripts/sqlite_database.py restore /app/data/backups/restore.sqlite3 --confirm-offline
if ($LASTEXITCODE -ne 0) { throw "Restore failed; keep the application stopped." }
docker compose up -d app
if ($LASTEXITCODE -ne 0) { throw "Could not start the application." }
curl.exe --fail http://localhost:8000/api/health
if ($LASTEXITCODE -ne 0) { throw "Application health check failed." }
```

Keep the pre-restore copy until application health and representative user data
have been checked. The restore tool is for file-based SQLite only. For other
database engines, use the vendor's backup and restore tooling; PostgreSQL is
not certified by this deployment.

## Schema changes and image rollback

1. Record the current image identifier and database backup.
2. Review `backend/alembic` for the target migration and its downgrade behavior.
3. Run migrations in a maintenance window against a restored copy before a
   production database. The application upgrades tracked databases at startup.
4. If the new image fails, stop it and restore the compatible database backup
   before starting the prior image. Never assume a downgrade is safe merely
   because Alembic provides a downgrade function.
5. Verify `/api/health`, key user flows, and application logs after recovery.

Legacy unversioned schemas that do not match the current lifecycle contract
must not be stamped as current; preserve a copy and follow the migration error
instructions rather than manually changing `alembic_version`.

## Health, limits, and incident response

- `/api/health` is an unauthenticated liveness/readiness signal; it returns
  HTTP 503 when the database probe fails. Do not use it to infer provider
  delivery or device availability.
- `/api/metrics` requires authentication. Metrics use route templates rather
  than user-supplied path segments. Counters are process-local; they are not an
  aggregated multi-worker monitoring backend.
- Redis-backed rate limits are shared across workers while Redis is healthy.
  The in-memory fallback is bounded and intentionally process-local; it is not
  an acceptable substitute for production multi-worker capacity testing.
- In a suspected credential or account incident, restrict external access,
  stop affected integrations, revoke provider credentials and user sessions,
  preserve relevant logs/backups, rotate keys with the documented credential
  migration, then verify the recovery before re-enabling actions.
- Provider webhooks must use their configured signature verification. Rotate
  webhook secrets at the provider and update Jalora configuration as one
  controlled change.

## Release and acceptance limitations

CI now builds the frontend and runs the Chromium acceptance suite without
converting browser-install or test failures into a successful skipped job.
Local tests cover authentication, secret redaction, signed webhook behavior,
SSRF filtering, task/approval isolation, backup/restore, and migration
round-trips. These do not replace:

- independent threat-model/security review and resolution of findings;
- production database, multi-worker concurrency, load-threshold, and rollback
  rehearsals;
- provider sandbox and native-device acceptance;
- operator-defined off-site backup, restore drills, and audit/data-retention
  policies; or
- a supported provider/OS matrix with terms and incident contacts.

Do not claim production readiness until these gates have evidence and owners.
