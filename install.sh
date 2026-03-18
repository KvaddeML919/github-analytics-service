#!/bin/bash
set -e

REPO_URL="https://github.com/KvaddeML919/GitHub-basic-stats-karteek.git"
INSTALL_DIR="$HOME/github-stats"
SHORTCUT="$HOME/Desktop/GitHub Stats.command"

echo ""
echo "========================================="
echo "  GitHub Team Stats — Installer"
echo "========================================="
echo ""

# --- Clone or update the repo ---
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only
else
    if [ -d "$INSTALL_DIR" ]; then
        echo "Error: $INSTALL_DIR exists but is not a git repo."
        echo "Remove it and re-run this installer."
        exit 1
    fi
    echo "Cloning repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

echo ""

# --- Install Python dependencies ---
echo "Installing Python dependencies..."
pip3 install --user -r requirements.txt 2>/dev/null || pip3 install -r requirements.txt
echo ""

# --- Set up org.txt ---
if [ ! -f "$INSTALL_DIR/org.txt" ]; then
    read -r -p "Enter the GitHub organization name: " org_name
    if [ -z "$org_name" ]; then
        echo "Error: No organization name provided."
        exit 1
    fi
    echo "$org_name" > "$INSTALL_DIR/org.txt"
    echo "Saved org: $org_name"
else
    echo "org.txt already exists — keeping existing org: $(cat "$INSTALL_DIR/org.txt")"
fi

echo ""

# --- Set up team.txt ---
if [ ! -f "$INSTALL_DIR/team.txt" ]; then
    echo "Setting up team members..."
    echo "Enter GitHub usernames (one per line). Press Enter on an empty line when done:"
    echo ""
    > "$INSTALL_DIR/team.txt"
    while true; do
        read -r -p "  Username: " username
        if [ -z "$username" ]; then
            break
        fi
        echo "$username" >> "$INSTALL_DIR/team.txt"
    done

    count=$(grep -c . "$INSTALL_DIR/team.txt" 2>/dev/null || echo 0)
    if [ "$count" -eq 0 ]; then
        echo ""
        echo "Warning: No usernames added. Edit $INSTALL_DIR/team.txt before running."
    else
        echo ""
        echo "Added $count team member(s)."
    fi
else
    echo "team.txt already exists — keeping existing team list."
fi

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
