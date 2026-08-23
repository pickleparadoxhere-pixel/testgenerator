FROM python:3.11-slim

WORKDIR /app

# Copy dependency definition & install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy complete application source
COPY . .

ENV PORT=10000
EXPOSE 10000

CMD ["python3", "backend/server.py"]
