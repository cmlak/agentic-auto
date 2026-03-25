# Agentic Automation Project: Deployment & Troubleshooting Manual

## I. Proactive Pre-Deployment Checklist
Before triggering any build or deployment to Cloud Run, superusers must verify the following to prevent failed builds or broken containers.

* **1. Verify Git as the Source of Truth:** Cloud Build deploys what is on GitHub, not what is floating unsaved in your Cloud Shell. 
    * *Action:* Always run `git status`. Ensure all working files (especially `requirements.txt` and `settings.py`) are committed and pushed (`git push origin main`) before deploying.
* **2. Audit Dependency Names:** Python package names in `requirements.txt` often differ from how they are imported in Django.
    * *Action:* Verify tricky packages. For example, ensure `django-import-export` is in the requirements file, even though it is listed as `import_export` in `INSTALLED_APPS`.
* **3. Confirm Dockerfile Integrity:** A successful Docker build does not guarantee a working app if the code isn't inside it.
    * *Action:* Ensure `COPY . .` exists in the Dockerfile so the actual Django project files are transferred into the container image.
* **4. Fortify View Logic:** Never assume a form submission contains all expected fields. 
    * *Action:* In `views.py`, strictly use `request.POST.get('fieldname')` instead of `request.POST['fieldname']`. The former returns `None` safely; the latter throws a hard Python crash (500 Server Error) if the key is missing.

---

## II. Production Configuration Standards
Misconfigurations in `settings.py` or Google Cloud cause 90% of deployment crashes. Enforce these standards:

* **The Debug Trap:** Security settings like `CSRF_TRUSTED_ORIGINS` and `SECURE_PROXY_SSL_HEADER` must sit outside of `if not DEBUG:` blocks. Otherwise, turning on `DEBUG = True` to troubleshoot will instantly break your login forms.
* **Database URL Formatting:** When using Cloud SQL via Unix sockets, the URL must be perfectly structured to avoid confusing the environment parser.
    * *Format:* `postgres://[USER]:[PASSWORD]@/[DATABASE_NAME]?host=/cloudsql/[PROJECT_ID]:[REGION]:[INSTANCE_NAME]`
* **Secret Manager Pinning:** Cloud Run caches secrets at boot. If you update a password in Secret Manager, Cloud Run will not see it until you deploy a new revision. Always ensure the Cloud Run variables tab is set to pull the `latest` version of a secret, not a pinned version like `1`.

---

## III. The Troubleshooting Matrix
If a deployment fails or the live site crashes, use this matrix to instantly identify the root cause.

| Symptom / Error | Probable Root Cause | Resolution |
| :--- | :--- | :--- |
| **Build Error:** `Cannot update environment variable to the given type` | Conflict between an old plaintext environment variable and a new Secret Manager secret. | Go to Cloud Run console > Variables & Secrets. Delete the plaintext variable with the same name. Redeploy. |
| **Browser Error:** `CSRF Verification Failed` | Cloud Run proxy is stripping HTTPS headers, or the domain isn't trusted. | Ensure `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')` and your Cloud Run URL is in `CSRF_TRUSTED_ORIGINS`. |
| **500 Server Error (On Page Load)** | The "Whitenoise Manifest Trap." A `{% static %}` tag is calling a file that doesn't exist. | Check HTML templates for broken static links, or temporarily change the storage backend to standard `StaticFilesStorage` to bypass strict checking. |
| **500 Server Error (On Form Submit)** | The database is empty, or Python crashed trying to read missing form data. | Run the manual database migration job. Verify custom views use `.get()` to handle missing data gracefully. |
| **Database Error:** `FATAL: password authentication failed` | Secret Manager holds the wrong password, or Cloud Run is caching an old secret version. | Verify the password in Cloud SQL. Update the string in Secret Manager. Force a new Cloud Run deployment to refresh the cache. |

---

## IV. Superuser Diagnostic Toolkit
When the matrix doesn't immediately solve the problem, use these Cloud Shell commands to force operations and expose hidden errors.

### 1. Fix Authentication Drops
If Cloud Shell refuses to deploy jobs, your session token expired:
`gcloud auth login`
`gcloud config set project document-project-464509`

### 2. Isolate Python Crashes in the Logs
Skip the generic HTTP traffic logs and search strictly for Python tracebacks (the exact line of code that failed) in the GCP Logs Explorer:
`resource.type="cloud_run_revision"`
`resource.labels.service_name="agentic-platform"`
`logName="projects/document-project-464509/logs/run.googleapis.com%2Fstderr"`
`severity>=ERROR`

### 3. Manually Force Database Migrations
If automated pipeline migrations fail, deploy a standalone job to build the database tables:
`gcloud run jobs deploy manual-migrate \`
  `--image gcr.io/document-project-464509/agentic-platform:latest \`
  `--region asia-southeast1 \`
  `--set-cloudsql-instances document-project-464509:asia-southeast1:agentic-platform \`
  `--set-secrets "DATABASE_URL=django_settings:latest" \`
  `--command "python" \`
  `--args "manage.py,migrate" \`
  `--execute-now`