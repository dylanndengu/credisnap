FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer — only rebuilds when requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/

# Railway / most hosts inject PORT at runtime; default to 8000 for local docker run
ENV PORT=8000

EXPOSE $PORT

CMD ["sh", "-c", "echo '=== ENV VARS ===' && env | sort && echo '===============' && uvicorn app.main:app --host 0.0.0.0 --port $PORT"]
