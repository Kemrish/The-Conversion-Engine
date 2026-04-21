FROM python:3.11-slim

WORKDIR /app

# Install Playwright system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium --with-deps

# Copy source
COPY . .

# Kill switch is OFF by default (data handling policy)
ENV LIVE_OUTBOUND_ENABLED=false
ENV LIVE_SMS_ENABLED=false

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
