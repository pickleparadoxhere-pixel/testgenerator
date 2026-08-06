FROM python:3.11-slim

WORKDIR /app

# Copy source code
COPY . .

# Expose port (Render injects PORT env variable)
ENV PORT=10000
EXPOSE 10000

# Run python3 backend/server.py
CMD ["python3", "backend/server.py"]
