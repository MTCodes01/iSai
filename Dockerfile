FROM python:3.12-slim

# Install FFmpeg and Git for audio processing and dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg git && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# The music directory will be mounted as a volume
VOLUME ["/app/music"]

# Run the bot manager
CMD ["python", "manager.py"]
