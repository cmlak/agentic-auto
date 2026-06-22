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
* **5. Cloud Run Jobs Require Management Commands:** Cloud Run Jobs are designed to execute synchronously and terminate upon completion.
    * *Action:* When setting up a scheduled task as a Cloud Run Job (e.g., scraping or backups), always create a new Django management command (e.g., `manage.py trigger_nbc_scraper`). If wrapping a Celery task, use `.apply()` instead of `.delay()` to ensure the code executes inline immediately within the container rather than dispatching it to a background worker.

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

V. Resilient Scraper Architecture
To bypass advanced firewalls (like Amazon CloudFront) and regional blocks, the scraper must use this specific configuration.

1. Regional Requirements
The National Bank of Cambodia (NBC) blocks Singapore data centers (asia-southeast1).

Requirement: All scraper Cloud Run Jobs must be deployed in us-central1.
2. Headless Browser Stack
Simple requests are blocked. We use undetected-chromedriver to mimic a human browser session.

Code Standard:
options = uc.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
driver = uc.Chrome(options=options)
try:
    driver.get(url)
    time.sleep(15) # Essential for CloudFront rendering
finally:
    driver.quit() # Critical to stop billing duration
Generated code may be subject to license restrictions not shown here. Use code with care. Learn more 

3. Dedicated Trigger (Cloud Scheduler)
Cloud Run Jobs require a POST request to the Google API, not the .run.app URL.

Target URI: https://run.googleapis.com/v2/projects/[PROJECT]/locations/us-central1/jobs/[JOB_NAME]:run
Auth: Use OAuth Token with the cloud-platform scope. 

VI. Pipeline & Deployment Guide: Cloud Run Jobs & Cloud Scheduler

This document outlines the standard operating procedures (SOP) for setting up, configuring, and deploying scheduled workloads using Cloud Run Jobs and Cloud Scheduler.

Architecture Overview
+------------------+                    +--------------------+                    +-----------------------+
|  Cloud Scheduler | --(HTTPS POST)-->  |   Cloud Run Job    | --(DB & API Call)--> | Postgres / Gemini API |
|   (Cron trigger) |  [OAuth OIDC/Auth] | (audit-agent-rules)|                      |  (using mounted Sec)  |
+------------------+                    +--------------------+                    +-----------------------+
Generated code may be subject to license restrictions not shown here. Use code with care. Learn more 

Cloud Scheduler runs on a cron schedule and triggers the Cloud Run Job via an authenticated Google HTTP endpoint.
Cloud Run Job runs the Django custom management command container.
Secret Manager securely stores and injects sensitive environment variables (DATABASE_URL, SECRET_KEY, GEMINI_API_KEY_2) directly into the Cloud Run container at runtime.

Step 1: Manage Secrets in Secret Manager
Do not package environment variables (especially secret keys or database credentials) inside the Docker image or local .env files for production deployments.

Store sensitive parameters in Google Cloud Secret Manager:

# 1. Create a secret for Django Settings (database URLs)
gcloud secrets create django_settings --replication-policy="automatic"

# 2. Add the actual connection string value
echo -n "postgis://user:pass@host:5432/db" | gcloud secrets versions add django_settings --data-file=-

# 3. Create a secret for the Gemini API Key
gcloud secrets create GEMINI_API_KEY_2 --replication-policy="automatic"
echo -n "YOUR_GEMINI_API_KEY" | gcloud secrets versions add GEMINI_API_KEY_2 --data-file=-
Generated code may be subject to license restrictions not shown here. Use code with care. Learn more 

Step 2: Deploy & Configure Cloud Run Jobs
When deploying or updating a Cloud Run Job, be aware of the --set-secrets trap. The --set-secrets flag overwrites the entire secret configuration. You must define all mapped secrets together in a single update command, or use --update-secrets for incremental changes.

The Overwrite Pitfall (Do Not Do This)
# BAD: This command will remove DATABASE_URL and only keep SECRET_KEY
gcloud run jobs update audit-agent-rules-job --set-secrets="SECRET_KEY=django_settings:latest"
Generated code may be subject to license restrictions not shown here. Use code with care. Learn more 

The Correct Multi-Secret Command (Do This)
Run the following command to bind all necessary environment variables to Secret Manager values simultaneously:

gcloud run jobs update audit-agent-rules-job \
  --region=asia-southeast1 \
  --set-secrets="DATABASE_URL=django_settings:latest,GEMINI_API_KEY_2=GEMINI_API_KEY_2:latest,SECRET_KEY=django_settings:latest"

Step 3: Trigger via Cloud Scheduler
Cloud Scheduler requires a service account to generate secure OAuth2 tokens to safely authenticate and invoke your Cloud Run Job.

1. Create a dedicated Invoker Service Account
gcloud iam service-accounts create scheduler-invoker \
  --display-name="Scheduler Invoker Service Account"

2. Grant the Cloud Run Invoker Role to the Service Account
This allows the service account to execute Cloud Run Jobs in your region:

gcloud run jobs add-iam-policy-binding audit-agent-rules-job \
  --member="serviceAccount:scheduler-invoker@document-project-464509.iam.gserviceaccount.com" \
  --role="roles/run.invoker" \
  --region=asia-southeast1

3. Create the Cloud Scheduler Job
Create the cron job using either the HTTP-target command (with target-specific run URLs) or the dedicated gcloud scheduler jobs create run command wrapper:

Option A: Dedicated Cloud Run Job Trigger (Recommended)
gcloud scheduler jobs create run audit-agent-rules-trigger \
  --location=asia-southeast1 \
  --schedule="0 6 * * *" \
  --job=audit-agent-rules-job \
  --region=asia-southeast1 \
  --service-account="scheduler-invoker@document-project-464509.iam.gserviceaccount.com"

Option B: Standard HTTP POST Trigger
gcloud scheduler jobs create http audit-agent-rules-trigger \
  --location=asia-southeast1 \
  --schedule="0 6 * * *" \
  --uri="https://asia-southeast1-run.googleapis.com/v2/projects/document-project-464509/locations/asia-southeast1/jobs/audit-agent-rules-job:run" \
  --http-method=POST \
  --oauth-service-account-email="scheduler-invoker@document-project-464509.iam.gserviceaccount.com" \
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"

Critical Django Bootstrap Pitfalls
1. The load_dotenv() Initialization Lifecycle
In Django, trying to load a local .env file inside a custom command file (commands/my_command.py) is too late. Django initializes settings.py before loading your custom command.

If you use .env files during local development, ensure load_dotenv() is called at the very top of manage.py and wsgi.py (before execute_from_command_line is invoked):

# manage.py
import os
import sys
from dotenv import load_dotenv

if __name__ == '__main__':
    if os.path.exists('.env'):
        load_dotenv()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'agentic_platform.settings')
    ...

2. Model Schema Accuracy (Unexpected Keyword Arguments)
Ensure that ORM calls within custom tasks match your exact database model schema. For instance, creating notifications using non-existent fields like notification_type will raise a crash:

# TypeError: AgentNotification() got unexpected keyword arguments: 'notification_type'
Generated code may be subject to license restrictions not shown here. Use code with care. Learn more 

Always verify field naming conventions (e.g. type vs notification_type) in models.py before building and shipping container versions to the cloud registry.

