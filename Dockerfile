# Use the official Python image
FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Step 1: Install system dependencies and Chrome browser requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    gnupg2 \
    wget \
    curl \
    lsb-release \
    ca-certificates \
    unzip \
    # Chrome dependencies
    libnss3 \
    libfontconfig1 \
    libxrender1 \
    libxtst6 \
    libv4l-0 \
    libasound2 \
    libgbm1 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Step 2: Install Google Chrome (Direct Download Method)
# This bypasses the repository 404 issue
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

# Step 3: Add official PostgreSQL apt repository and install v18
RUN mkdir -p /etc/apt/keyrings \
    && wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor --yes -o /etc/apt/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

# Step 4: Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Step 5: Copy your Django project code
COPY . .

# Step 6: Run collectstatic
RUN SECRET_KEY="dummy-key-for-build" DATABASE_URL=sqlite:///:memory: python manage.py collectstatic --noinput

# Step 7: Default command
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 agentic_platform.wsgi:application
