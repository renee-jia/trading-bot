FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY data_fetcher.py technical_analyzer.py sentiment_analyzer.py \
     trend_analyzer.py report_generator.py macro_analyzer.py main.py \
     daily_report.py email_sender.py alpaca_trader.py stock_discovery.py \
     covered_call_advisor.py sell_put_advisor.py daily_watch.py ./

# Private algorithm package (custom_alphas / scorer / strategy / configs).
# Git-ignored, but present in the local build context, so it is baked into the
# image here. Provide it when building from a fresh clone.
COPY core/ ./core/

# Secrets (ALPACA_*, REPORT_EMAIL_*) are injected at runtime as environment
# variables (e.g. Cloud Run --set-env-vars / Secret Manager), NOT baked in.
CMD ["python", "daily_report.py", "--trade"]
