# Use Python with Playwright pre-installed
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY templates/ templates/

# Expose port
EXPOSE 8080

# Run the application
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080", "--timeout", "120", "--workers", "2"]
