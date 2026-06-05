#!/bin/bash
#
# Install the daily stock analysis report as a macOS Launch Agent.
#
# What this does:
#   - Installs a launchd agent that runs daily_report.py at 8:00 AM
#   - If your Mac is asleep at 8 AM, it runs when the Mac wakes up
#   - Skips weekends automatically
#   - Emails the report to the address(es) you set in REPORT_EMAIL_TO (.env)
#
# Prerequisites:
#   1. Create a .env file with your Gmail credentials (see below)
#   2. Run: ./setup_daily.sh install
#
# Commands:
#   ./setup_daily.sh install    Install and start the daily agent
#   ./setup_daily.sh uninstall  Stop and remove the agent
#   ./setup_daily.sh status     Check if the agent is running
#   ./setup_daily.sh test       Run the analysis once right now
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.tradingbot.daily-report"
PLIST_TEMPLATE="$SCRIPT_DIR/$PLIST_NAME.plist.template"
PLIST_SRC="$SCRIPT_DIR/$PLIST_NAME.plist"   # rendered from template; git-ignored
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
LOG_DIR="$SCRIPT_DIR/logs"
ENV_FILE="$SCRIPT_DIR/.env"

install() {
    echo "=== Installing Daily Stock Report Agent ==="
    echo ""

    # Check .env
    if [ ! -f "$ENV_FILE" ]; then
        echo "Creating .env template..."
        cat > "$ENV_FILE" << 'EOF'
# Gmail credentials for daily report emails
# Get an app password at: https://myaccount.google.com/apppasswords
REPORT_EMAIL_FROM=your-gmail@gmail.com
REPORT_EMAIL_PASSWORD=your-16-char-app-password
REPORT_EMAIL_TO=recipient@example.com
EOF
        echo ""
        echo "IMPORTANT: Edit .env with your Gmail credentials before the agent can send emails."
        echo "  File: $ENV_FILE"
        echo "  Get app password: https://myaccount.google.com/apppasswords"
        echo ""
    fi

    # Create log directory
    mkdir -p "$LOG_DIR"

    # Render the (git-ignored) plist from the tracked template, substituting the
    # project path and injecting email credentials from .env. The template never
    # contains secrets; the rendered plist is local-only.
    if [ ! -f "$PLIST_TEMPLATE" ]; then
        echo "Error: missing template $PLIST_TEMPLATE"
        exit 1
    fi
    FROM=""; PASS=""; TO=""
    if [ -f "$ENV_FILE" ] && ! grep -q "your-gmail@gmail.com" "$ENV_FILE" 2>/dev/null; then
        FROM=$(grep '^REPORT_EMAIL_FROM' "$ENV_FILE" | cut -d= -f2-)
        PASS=$(grep '^REPORT_EMAIL_PASSWORD' "$ENV_FILE" | cut -d= -f2-)
        TO=$(grep '^REPORT_EMAIL_TO' "$ENV_FILE" | cut -d= -f2-)
        echo "Email credentials loaded from .env"
    else
        echo "Warning: .env missing or has placeholder values. Emails won't send until configured."
    fi

    PROJECT_DIR="$SCRIPT_DIR" FROM="$FROM" PASS="$PASS" TO="$TO" \
    python3 -c "
import os, plistlib
with open('$PLIST_TEMPLATE', 'rb') as f:
    raw = f.read().replace(b'__PROJECT_DIR__', os.environ['PROJECT_DIR'].encode())
plist = plistlib.loads(raw)
env = plist.get('EnvironmentVariables', {})
env['REPORT_EMAIL_FROM'] = os.environ.get('FROM', '')
env['REPORT_EMAIL_PASSWORD'] = os.environ.get('PASS', '')
env['REPORT_EMAIL_TO'] = os.environ.get('TO', '')
plist['EnvironmentVariables'] = env
with open('$PLIST_SRC', 'wb') as f:
    plistlib.dump(plist, f)
"
    echo "Rendered plist -> $PLIST_SRC"

    # Unload existing agent if present
    launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null || true

    # Copy plist to LaunchAgents
    cp "$PLIST_SRC" "$PLIST_DST"
    echo "Installed plist to $PLIST_DST"

    # Load the agent
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
    echo "Agent loaded and scheduled for 8:00 AM daily."
    echo ""
    echo "Logs:"
    echo "  stdout: $LOG_DIR/daily_report.stdout.log"
    echo "  stderr: $LOG_DIR/daily_report.stderr.log"
    echo ""
    echo "Done! The report will run at 8 AM every trading day."
    echo "If your Mac is asleep at 8 AM, it runs when you open the lid."
}

uninstall() {
    echo "=== Uninstalling Daily Stock Report Agent ==="
    launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "Agent removed."
}

status() {
    echo "=== Daily Stock Report Agent Status ==="
    if launchctl print "gui/$(id -u)/$PLIST_NAME" 2>/dev/null; then
        echo ""
        echo "Agent is LOADED and scheduled."
    else
        echo "Agent is NOT loaded."
        echo "Run: ./setup_daily.sh install"
    fi
    echo ""
    if [ -f "$LOG_DIR/daily_report.stdout.log" ]; then
        echo "Last 5 lines of stdout log:"
        tail -5 "$LOG_DIR/daily_report.stdout.log"
    fi
}

test_run() {
    echo "=== Running Analysis Now (test) ==="
    # Load env vars
    if [ -f "$ENV_FILE" ]; then
        export $(grep -v '^#' "$ENV_FILE" | xargs)
    fi
    cd "$SCRIPT_DIR"
    source "$SCRIPT_DIR/.venv_trading/bin/activate"
    python daily_report.py --force
}

case "${1:-install}" in
    install)   install ;;
    uninstall) uninstall ;;
    status)    status ;;
    test)      test_run ;;
    *)
        echo "Usage: $0 {install|uninstall|status|test}"
        exit 1
        ;;
esac
