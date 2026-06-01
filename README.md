# MiNDFIRL 2026 Technical Handoff

Production app: <https://mindfirl-2026-g8hme5fehsggdqf4.centralus-01.azurewebsites.net>  
Active deployment branch: `tee`  
Application root for deployment: `UI/`

MiNDFIRL is an interactive record linkage demonstration app. It lets users review record pairs, reveal additional values in privacy-preserving mode, submit match/non-match decisions, and gives administrators tools to export, inspect, restore, and analyze collected responses.

## Current App Status

### Live

- Public landing page at `/` with links to the current demos.
- Privacy-preserving desktop workflow at `/privacy_desktop`.
- Full-disclosure desktop workflow at `/disclosing_desktop`.
- Full-disclosure mobile workflow at `/mobile`.
- User response capture through `/update_selection` and `/submit_selections`.
- Redis-backed per-user session state, temporary selections, final responses, privacy risk metrics, and reveal-level snapshots.
- CSRF protection on mutating user/admin requests.
- Admin dashboard at `/admin`, protected by a shared password in `ADMIN_PASSWORD`.
- Admin response summary view at `/admin/results`.
- Admin CSV export at `/admin/download_redis_data`.
- Admin CSV upload/import at `/admin/upload_csv` and `/admin/upload_data_csv`; the uploaded CSV must match the app's exported report format.
- Admin graph/analytics page at `/admin/export_graph`, including date-window filtering and experiment filtering.
- Admin experiment management at `/admin/experiments`, including creating labels by datetime range, assigning ranges, relabeling, and clearing labels.
- Raw Redis browser at `/admin/view_all_redis_data`.
- Redis wipe control at `/admin/clear_redis`.
- Health endpoint at `/healthz`; returns `200` when Redis responds and `503` when Redis is unavailable.
- GitHub Actions deployment from the `tee` branch to Azure App Service `mindfirl-2026`.

### Pending Or Incomplete

- There is no automated test suite in the repo.
- There is no Dockerfile or local container workflow.
- The app currently supports one shared admin password rather than named admin accounts, roles, or audit trails.
- The Redis admin clear action calls `flushall`, so it deletes every key in the connected Redis database, not just MiNDFIRL-prefixed keys.
- The app writes uploaded admin reports to `UI/data/uploaded_admin_report.csv`. On Azure App Service this file should be treated as ephemeral deployment/runtime storage, not durable backup storage.
- CSV loading is simple line splitting in `UI/data_loader.py`; quoted commas or richer CSV formatting are not supported.
- `UI/data_model.py` contains a TODO for paging support in `DataPairList`.
- Admin login lockout is session-cookie based. It slows repeated attempts from one browser session but is not a global IP/account lockout.

### Known Bug/Risk Areas

- `FLASK_SECRET_KEY` and `ADMIN_PASSWORD` are required at startup. Missing values will fail app boot.
- Redis availability is critical. Public demo pages and admin reporting depend on Redis reads/writes.
- `APP_ENV=production` should be set in Azure so secure-cookie defaults are production-aware.
- `SESSION_COOKIE_SECURE` should be enabled in production because the app is served over HTTPS.
- User identity is stored in a `user_id` cookie. Clearing browser cookies starts a new user identity.
- The deployed workflow excludes `.env`; production secrets must be configured in Azure App Service settings and GitHub repository secrets.
- Logs currently include some user/session identifiers through `logging.error(...)` calls in normal request paths.

## Deployment Workflow

### Production: Azure App Service Through GitHub Actions

The production deployment is defined in `.github/workflows/tee_mindfirl-2026.yml`.

1. Confirm changes are committed on the `tee` branch.
2. Push to `tee`.
3. GitHub Actions runs `Build and deploy Python app to Azure Web App - mindfirl-2026` when files under `UI/**` or the workflow file change. It can also be run manually with `workflow_dispatch`.
4. The build job checks out the repo, installs Python `3.11`, creates a temporary virtual environment, and installs `UI/requirements.txt`.
5. The workflow zips the contents of `UI/` into `app.zip`.
6. The zip excludes `.env`, local virtual environments, Python cache files, `resultsEnv`, and downloaded log archives.
7. The deploy job downloads `app.zip` and deploys it to Azure App Service `mindfirl-2026`, slot `Production`, with `azure/webapps-deploy@v3`.
8. Deployment authentication uses GitHub secret `AZUREAPPSERVICE_PUBLISHPROFILE_73C1B3492C1A48EFA3826B5077C1E6F3`.
9. After deploy, verify:
   - `https://mindfirl-2026-g8hme5fehsggdqf4.centralus-01.azurewebsites.net/`
   - `https://mindfirl-2026-g8hme5fehsggdqf4.centralus-01.azurewebsites.net/healthz`
   - `/admin` with the current `ADMIN_PASSWORD`

### Azure App Service Configuration

Set these app settings in Azure App Service Configuration:

- `FLASK_SECRET_KEY`: long random Flask session secret.
- `ADMIN_PASSWORD`: current shared admin password.
- `APP_ENV`: set to `production`.
- `SESSION_COOKIE_SECURE`: set to `true`.
- `SESSION_COOKIE_SAMESITE`: usually `Lax`.
- `REDIS_URL`: preferred single Redis connection string.

If `REDIS_URL` is not used, set the split Redis settings instead:

- `REDIS_HOST`
- `REDIS_PORT`
- `REDIS_USERNAME`
- `REDIS_PASSWORD`
- `REDIS_USE_TLS`

Optional runtime settings:

- `PORT`: Azure normally provides this.
- `WEB_CONCURRENCY`: Gunicorn worker count; default in `startup.sh` is `2`.
- `GUNICORN_TIMEOUT`: default in `startup.sh` is `120`.

### Azure Startup Command

Use the app startup script:

```bash
bash startup.sh
```

The script runs from `UI/`, loads `.env` only if present, then starts:

```bash
gunicorn --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY} --timeout ${GUNICORN_TIMEOUT} app:app
```

### Local Development

From the repo root:

```powershell
cd UI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\load-env.ps1
python app.py
```

For local Redis, either set `REDIS_URL` in `UI/.env` or run a Redis instance on `localhost:6379`.

## Ongoing Maintenance

- Monitor `/healthz` for Redis health.
- Review Azure App Service logs after each deployment.
- Export admin CSVs regularly from `/admin/download_redis_data` if response data must be retained outside Redis.
- Back up Redis before using `/admin/clear_redis`; the action is irreversible and currently uses `flushall`.
- Rotate `ADMIN_PASSWORD` when administrators change or after demos/events.
- Rotate `FLASK_SECRET_KEY` only with awareness that existing Flask sessions will be invalidated.
- Rotate Redis credentials according to the hosting provider's policy and update Azure App Service settings.
- Rotate the Azure publish profile and update GitHub secret `AZUREAPPSERVICE_PUBLISHPROFILE_73C1B3492C1A48EFA3826B5077C1E6F3` when Azure credentials change.
- Keep Python dependencies in `UI/requirements.txt` current and retest before pushing to `tee`.
- Treat `UI/data/ppirl.csv`, `UI/data/section2.csv`, and `UI/data/settings.csv` as app data/configuration inputs. Changes to their format can affect display, privacy metrics, and admin exports.
- Add regression tests for response submission, Redis snapshot building, admin CSV export/import, and `/healthz` before making larger changes.

## Dependencies

### Python Runtime

- Python `3.11` in GitHub Actions.
- Flask app entry point: `UI/app.py`.
- WSGI production server: Gunicorn through `UI/startup.sh`.

### Python Packages

Defined in `UI/requirements.txt`:

- `Flask==3.1.1`
- `redis==6.2.0`
- `gunicorn==25.0.3`
- `pandas==3.0.0`

### Frontend Assets

- Jinja templates under `UI/templates/`.
- Static CSS, JavaScript, and images under `UI/static/`.
- Local Bootstrap files are checked into `UI/static/css/bootstrap.min.css` and `UI/static/javascript/bootstrap.min.js`.

### Data Files

- `UI/data/ppirl.csv`: primary pair data used by the public workflows.
- `UI/data/section2.csv`: dataset used for privacy-risk calculations.
- `UI/data/settings.csv`: simple key/value settings, currently including `privacy_budget`.
- `UI/data/uploaded_admin_report.csv`: latest admin-uploaded report copy.

### External Services

- Azure App Service: hosts production app `mindfirl-2026`.
- Azure Redis or compatible Redis service: stores all active user/admin response state.
- GitHub Actions: builds and deploys from `tee`.
- GitHub repository secrets: stores the Azure publish profile used by deployment.

### Environment Variables

Required:

- `FLASK_SECRET_KEY`
- `ADMIN_PASSWORD`
- Redis connection via either `REDIS_URL` or split Redis variables.

Redis variables:

- `REDIS_URL`
- `REDIS_HOST`
- `REDIS_PORT`
- `REDIS_USERNAME`
- `REDIS_PASSWORD`
- `REDIS_USE_TLS`

Production/session variables:

- `APP_ENV`
- `SESSION_COOKIE_SECURE`
- `SESSION_COOKIE_SAMESITE`

Runtime variables:

- `PORT`
- `WEB_CONCURRENCY`
- `GUNICORN_TIMEOUT`

## Admin Access

Admin access is managed by one shared password:

1. Set `ADMIN_PASSWORD` in Azure App Service Configuration.
2. Restart the app after changing it.
3. Share the new password only with current administrators.
4. Administrators sign in from the landing page admin form or by posting the password to `/admin`.

To transfer administrative ownership:

1. Add the new owner to the Azure subscription/resource group with permission to manage App Service settings.
2. Add the new owner to the GitHub repository with permission to edit Actions secrets and deploy from `tee`.
3. Give the new owner access to the Redis resource or Redis provider account.
4. Rotate `ADMIN_PASSWORD`.
5. Rotate the Azure App Service publish profile and update the GitHub Actions secret.
6. Confirm the new owner can deploy from `tee`, view `/healthz`, and sign in to `/admin`.
7. Remove former owners from Azure, GitHub, Redis, and any password vault/shared secret store.

There are no per-user admin accounts in the app today, so accountability depends on external access controls around Azure, GitHub, Redis, and the shared `ADMIN_PASSWORD`.

## Project Background

Record linkage identifies the same entities across one or more databases when there is no unique identifier.

The MiNDFIRL hybrid record linkage framework combines automated and manual review. Automated algorithms resolve high-confidence matches/non-matches at scale, while ambiguous pairs can be sent to human reviewers for final determination. The interactive demonstration in `UI/` emulates the human review portion and includes privacy-preserving disclosure controls.

Additional project information: <https://pinformatics.org/ppirl/mindfirl.php>
