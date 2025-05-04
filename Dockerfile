FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Install uv using pip
RUN pip install --no-cache-dir uv

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies using uv
# uv should be in PATH now after pip install
# Use --system flag to install in the global environment
RUN uv pip install --system --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Environment variables
ENV FLASK_APP=wsgi.py
ENV FLASK_ENV=production

# Expose port (internal container port)
EXPOSE 5000

# Command to run the application using python directly for debugging
# CMD ["python", "-c", "print('Attempting to create app...'); from app import create_app; app = create_app(); print('App created successfully!')"]
# Original command:
# CMD ["gunicorn", "--bind", "0.0.0.0:5000", "wsgi:app"]
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "3600", "wsgi:app"]
