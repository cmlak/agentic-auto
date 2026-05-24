# Use the official Python image
FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Install system dependencies for build tools and repository setups
# Added curl to explicitly break the layer cache and force a clean apt rebuild
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    gnupg2 \
    wget \
    curl \
    lsb-release \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Add official PostgreSQL apt repository using the modern keyring method and install v18
# Explicitly updating the certificate authorities ensures the signing verification handshake clears flawlessly.
RUN mkdir -p /etc/apt/keyrings \
    && wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor --yes -o /etc/apt/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your Django project code
COPY . .

# Run collectstatic with a dummy database variable
RUN SECRET_KEY="dummy-key-for-build" DATABASE_URL=sqlite:///:memory: python manage.py collectstatic --noinput

# Run gunicorn on the port Cloud Run expects (8080)
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 agentic_platform.wsgi:application