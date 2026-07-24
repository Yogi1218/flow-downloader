# Use an official Python runtime as a base image
FROM python:3.11-slim

# Install system dependencies including ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory inside the container
WORKDIR /app

# Copy python dependency configs
COPY requirements.txt .

# Install dependencies (yt-dlp is installed from master branch inside pip to ensure latest fixes)
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install -U --no-cache-dir https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz

# Copy the rest of the application files
COPY . .

# Expose port 8080 for Flask server
EXPOSE 8080

# Run gunicorn WSGI server
CMD ["gunicorn", "-b", "0.0.0.0:8080", "server:app"]
