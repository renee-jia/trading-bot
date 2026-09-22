FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy every top-level module. An explicit file list previously dropped
# market_bars.py after it was added, and Cloud Run then died on import.
COPY *.py ./

# Private algorithm package (custom_alphas / scorer / strategy / configs).
# Git-ignored, but present in the local build context, so it is baked into the
# image here. Provide it when building from a fresh clone.
COPY core/ ./core/

# Secrets (ALPACA_*, REPORT_EMAIL_*) are injected at runtime as environment
# variables (e.g. Cloud Run --set-env-vars / Secret Manager), NOT baked in.
CMD ["python", "daily_report.py", "--trade"]
