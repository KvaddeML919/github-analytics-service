#!/bin/bash
# GitHub Team Stats — macOS / Linux installer (Windows: install.ps1)
set -e

REPO_URL="https://github.com/KvaddeML919/github-analytics-service.git"
INSTALL_DIR="$HOME/github-stats"
SHORTCUT="$HOME/Desktop/GitHub Stats.command"

fail() {
    echo ""
    echo "Error: $1"
    if [ -n "${2:-}" ]; then
        echo "$2"
    fi
    exit 1
}

echo ""
echo "========================================="
echo "  GitHub Team Stats — Installer"
echo "========================================="
echo ""

# --- Preflight checks ---
echo "Checking prerequisites..."

if ! command -v git >/dev/null 2>&1; then
    fail "Git is not installed or not on PATH." \
        "  Install: xcode-select --install
  Or download from https://git-scm.com"
fi
echo "  Git: OK"

if ! command -v python3 >/dev/null 2>&1; then
    fail "Python 3 is not installed or not on PATH." \
        "  Install from https://www.python.org/downloads/
  Then re-run this installer."
fi
echo "  Python 3: OK ($(python3 --version 2>&1))"

if ! python3 -m pip --version >/dev/null 2>&1; then
    fail "pip is not available for python3." \
        "  Run: python3 -m ensurepip --upgrade
  Or: python3 -m pip install --upgrade pip
  Then re-run this installer."
fi
echo "  pip: OK ($(python3 -m pip --version 2>&1))"

echo ""

# --- Clone or update the repo ---
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only
else
    if [ -d "$INSTALL_DIR" ]; then
        fail "$INSTALL_DIR exists but is not a git repo." \
            "  Remove the folder and re-run this installer:
  rm -rf \"$INSTALL_DIR\""
    fi
    echo "Cloning repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

echo ""

# --- Install Python dependencies ---
echo "Installing Python dependencies..."
if python3 -m pip install --user -r requirements.txt; then
    :
elif python3 -m pip install -r requirements.txt; then
    echo "  Note: --user install failed; installed without --user instead."
else
    fail "Failed to install Python dependencies (see pip output above)." \
        "  Manual recovery:
  cd \"$INSTALL_DIR\"
  python3 -m pip install -r requirements.txt
  python3 -c \"import requests, openpyxl; print('OK')\"
  python3 github_stats.py"
fi

echo "Verifying dependencies..."
if ! python3 -c "import requests, openpyxl; print('  Dependencies OK')"; then
    fail "Dependencies installed but import check failed." \
        "  Manual recovery:
  cd \"$INSTALL_DIR\"
  python3 -m pip install -r requirements.txt
  python3 -c \"import requests, openpyxl; print('OK')\"
  python3 github_stats.py"
fi

echo ""

# --- Set up org.txt ---
if [ ! -f "$INSTALL_DIR/org.txt" ]; then
    read -r -p "Enter the GitHub organization name: " org_name
    if [ -z "$org_name" ]; then
        fail "No organization name provided."
    fi
    echo "$org_name" > "$INSTALL_DIR/org.txt"
    echo "Saved org: $org_name"
else
    echo "org.txt already exists — keeping existing org: $(cat "$INSTALL_DIR/org.txt")"
fi

echo ""

# --- Set up api.txt ---
if [ ! -f "$INSTALL_DIR/api.txt" ]; then
    read -r -p "GitHub API base URL [https://api.github.com]: " api_url
    api_url="${api_url:-https://api.github.com}"
    api_url="${api_url%/}"
    if [ "$api_url" != "https://api.github.com" ]; then
        echo "$api_url" > "$INSTALL_DIR/api.txt"
        echo "Saved API base: $api_url"
    else
        echo "Using GitHub.com API."
    fi
else
    echo "api.txt already exists — keeping existing API base: $(cat "$INSTALL_DIR/api.txt")"
fi

echo ""

# --- Set up team.txt ---
if [ ! -f "$INSTALL_DIR/team.txt" ]; then
    echo "Setting up teams and members..."
    echo "You'll enter team names first, then GitHub usernames for each team."
    echo "Press Enter on an empty line to finish each step."
    echo ""
    > "$INSTALL_DIR/team.txt"
    total_members=0

    while true; do
        read -r -p "  Team name (empty to finish adding teams): " team_name
        if [ -z "$team_name" ]; then
            break
        fi

        echo "[$team_name]" >> "$INSTALL_DIR/team.txt"
        echo "    Adding members to $team_name..."

        team_count=0
        while true; do
            read -r -p "      Username (empty to finish this team): " username
            if [ -z "$username" ]; then
                break
            fi
            echo "$username" >> "$INSTALL_DIR/team.txt"
            team_count=$((team_count + 1))
        done

        echo "" >> "$INSTALL_DIR/team.txt"
        total_members=$((total_members + team_count))
        echo "    Added $team_count member(s) to $team_name."
        echo ""
    done

    if [ "$total_members" -eq 0 ]; then
        echo ""
        echo "Warning: No members added. Edit $INSTALL_DIR/team.txt before running."
    else
        echo ""
        echo "Added $total_members total team member(s)."
    fi
else
    echo "team.txt already exists — keeping existing team list."
fi

echo ""

# --- Set up additional organization profiles ---
while true; do
    read -r -p "Add another organization profile? [y/N]: " add_profile
    case "$add_profile" in
        y|Y|yes|YES) ;;
        *) break ;;
    esac

    read -r -p "  Profile name (local label, e.g. enterprise-team): " profile_name
    if [ -z "$profile_name" ] || [[ "$profile_name" == */* ]] || [[ "$profile_name" == .* ]]; then
        echo "  Invalid profile name. Use letters, numbers, hyphens, or underscores."
        continue
    fi

    profile_dir="$INSTALL_DIR/profiles/$profile_name"
    if [ -e "$profile_dir" ]; then
        echo "  Profile already exists: $profile_name"
        continue
    fi
    mkdir -p "$profile_dir"

    read -r -p "  Organization name: " profile_org
    if [ -z "$profile_org" ]; then
        rm -rf "$profile_dir"
        echo "  Organization name is required; profile was not created."
        continue
    fi
    echo "$profile_org" > "$profile_dir/org.txt"

    read -r -p "  GitHub API base URL [https://api.github.com]: " profile_api
    profile_api="${profile_api:-https://api.github.com}"
    profile_api="${profile_api%/}"
    if [ "$profile_api" != "https://api.github.com" ]; then
        echo "$profile_api" > "$profile_dir/api.txt"
    fi

    echo "  Setting up teams for $profile_name..."
    > "$profile_dir/team.txt"
    while true; do
        read -r -p "    Team name (empty to finish): " profile_team
        [ -z "$profile_team" ] && break
        echo "[$profile_team]" >> "$profile_dir/team.txt"
        while true; do
            read -r -p "      Username (empty to finish this team): " profile_user
            [ -z "$profile_user" ] && break
            echo "$profile_user" >> "$profile_dir/team.txt"
        done
        echo "" >> "$profile_dir/team.txt"
    done
    echo "  Created profile: $profile_name"
    echo "  Token will be requested when this profile is run."
    echo ""
done

echo ""

# --- Create desktop shortcut ---
cat > "$SHORTCUT" << 'LAUNCHER'
#!/bin/bash
cd "$HOME/github-stats" || { echo "Error: $HOME/github-stats not found. Re-run the installer."; read -r -p "Press Enter to close..."; exit 1; }
echo ""
echo "========================================="
echo "  GitHub Team Stats"
echo "========================================="
echo ""
python3 -c "import requests, openpyxl" 2>/dev/null || {
    echo "Error: Missing dependencies. Run in Terminal:"
    echo "  cd ~/github-stats && python3 -m pip install -r requirements.txt"
    read -r -p "Press Enter to close..."
    exit 1
}
python3 github_stats.py
echo ""
echo "-----------------------------------------"
read -r -p "Press Enter to close..."
LAUNCHER
chmod +x "$SHORTCUT"

echo "Desktop shortcut created: GitHub Stats"
echo ""
echo "========================================="
echo "  Installation complete!"
echo "========================================="
echo ""
echo "  To run:  Double-click 'GitHub Stats' on your Desktop"
echo "  To edit team list:  $INSTALL_DIR/team.txt"
echo ""
