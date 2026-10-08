#!/usr/bin/env bash
# =============================================================================
# MacBook Audio Control Center - Uninstaller
# =============================================================================

set -e

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${YELLOW}======================================================================${NC}"
echo -e "${YELLOW}       MacBook Audio Control Center - Uninstallation Script          ${NC}"
echo -e "${YELLOW}======================================================================${NC}"

# Stop and disable systemd service
echo -e "\n${BLUE}--> Stopping and disabling background watchdog daemon...${NC}"
systemctl --user stop macbook-audio-watchdog.service 2>/dev/null || true
systemctl --user disable macbook-audio-watchdog.service 2>/dev/null || true
rm -f "${HOME}/.config/systemd/user/macbook-audio-watchdog.service"
systemctl --user daemon-reload 2>/dev/null || true

# Remove binaries
echo -e "${BLUE}--> Removing binaries from ~/.local/bin...${NC}"
rm -f "${HOME}/.local/bin/macbook-audio-control"
rm -f "${HOME}/.local/bin/audio-watchdog-rs"
rm -f "${HOME}/.local/bin/audio-query-rs"

# Remove desktop entry
echo -e "${BLUE}--> Removing desktop entry...${NC}"
rm -f "${HOME}/.local/share/applications/macbook-audio-control.desktop"
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "${HOME}/.local/share/applications" &>/dev/null || true
fi

# Remove application files
echo -e "${BLUE}--> Removing ~/.local/share/macbook-audio-control...${NC}"
rm -rf "${HOME}/.local/share/macbook-audio-control"

# Prompt regarding PipeWire/WirePlumber configs
read -p "Do you also want to remove custom PipeWire & WirePlumber drop-in configs? (y/N): " -r choice
if [[ "$choice" =~ ^[Yy]$ ]]; then
    rm -f "${HOME}/.config/pipewire/pipewire.conf.d/10-quantum.conf"
    rm -f "${HOME}/.config/wireplumber/wireplumber.conf.d/51-pro-audio-fix.conf"
    rm -f "${HOME}/.config/wireplumber/wireplumber.conf.d/52-bluez-bitrate.conf"
    echo -e "${GREEN}Removed audio configuration drops.${NC}"
    systemctl --user restart pipewire wireplumber 2>/dev/null || true
else
    echo -e "${YELLOW}Preserved PipeWire/WirePlumber configs in ~/.config/.${NC}"
fi

echo -e "\n${GREEN}MacBook Audio Control Center uninstalled successfully.${NC}\n"
