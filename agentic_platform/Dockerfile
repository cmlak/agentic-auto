# Use the official Python image
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Install system dependencies for Postgres (psycopg2)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your Django project code
COPY . .
RUN python manage.py collectstatic --noinput

# Run gunicorn on the port Cloud Run expects (8080)
# Replace 'agentic_platform' with your actual project folder name
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 agentic_platform.wsgi:application