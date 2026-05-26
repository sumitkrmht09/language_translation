# Use official lightweight Python base image
FROM python:3.10-slim

# Install git so the app can query the git commit hash at runtime
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Set environment variables to prevent Python from writing pyc files and to buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Set working directory inside the container
WORKDIR /app

# Install system dependencies required by OpenCV (cv2) and PyMuPDF (fitz)
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     libgl1-mesa-glx \
#     libglib2.0-0 \
#     gcc \
#     python3-dev \
#     && rm -rf /var/lib/apt/lists/*

# Copy only the requirements first to leverage Docker layer caching
COPY requirements.txt /app/

# Install Python package dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all remaining source code files into the container
COPY . /app/

# Expose the network port
EXPOSE 8000

# Start the FastAPI application using uvicorn
CMD uvicorn app:app --host 0.0.0.0 --port $PORT
