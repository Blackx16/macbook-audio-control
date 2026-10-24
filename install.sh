#!/usr/bin/env bash
# =============================================================================
# MacBook Audio Control Center - Installer
# Works on Arch Linux, CachyOS, Fedora, Ubuntu, Debian, and modern Linux distros
# =============================================================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================================================${NC}"
echo -e "${BLUE}       🍎 MacBook Audio Control Center - Installation Script          ${NC}"
echo -e "${BLUE}======================================================================${NC}"

# Directories
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HOME}/.local/share/macbook-audio-control"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/.local/share/applications"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
PIPEWIRE_CONF_DIR="${HOME}/.config/pipewire/pipewire.conf.d"
WIREPLUMBER_CONF_DIR="${HOME}/.config/wireplumber/wireplumber.conf.d"

mkdir -p "${INSTALL_DIR}"
mkdir -p "${BIN_DIR}"
mkdir -p "${DESKTOP_DIR}"
mkdir -p "${SYSTEMD_DIR}"
mkdir -p "${PIPEWIRE_CONF_DIR}"
mkdir -p "${WIREPLUMBER_CONF_DIR}"

echo -e "\n${BLUE}[1/7] Checking system prerequisites...${NC}"

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}Error: python3 is not installed.${NC}" >&2
    exit 1
fi
echo -e "  ✓ Python 3: $(python3 --version)"

# Check PyQt6 or PyQt5
if python3 -c "import PyQt6" &>/dev/null; then
    echo -e "  ✓ PyQt: PyQt6 detected"
elif python3 -c "import PyQt5" &>/dev/null; then
    echo -e "  ✓ PyQt: PyQt5 detected"
else
    echo -e "${YELLOW}  ! Warning: Neither PyQt6 nor PyQt5 found in Python environment.${NC}"
    echo -e "    You can install it via:"
    echo -e "    - Arch/CachyOS: sudo pacman -S python-pyqt6"
    echo -e "    - Fedora:       sudo dnf install python3-pyqt6"
    echo -e "    - Ubuntu/Debian:sudo apt install python3-pyqt6"
fi

# Check Cargo / Rust
if ! command -v cargo &>/dev/null; then
    echo -e "${RED}Error: Rust toolchain (cargo) is required to build the high-speed audio modules.${NC}" >&2
    echo -e "Please install Rust (e.g. 'sudo pacman -S rust cargo' or 'curl https://sh.rustup.rs -sSf | sh')" >&2
    exit 1
fi
echo -e "  ✓ Rust toolchain: $(cargo --version)"

# Check PipeWire & ALSA tools
if ! command -v pw-metadata &>/dev/null || ! command -v amixer &>/dev/null; then
    echo -e "${YELLOW}  ! Warning: pw-metadata or amixer command not found.${NC}"
    echo -e "    Make sure pipewire, wireplumber, and alsa-utils are installed."
else
    echo -e "  ✓ PipeWire & ALSA utilities present"
fi

echo -e "\n${BLUE}[2/7] Compiling Rust acceleration & watchdog modules...${NC}"
cd "${PROJECT_DIR}/rust"
cargo build --release

BUILD_TARGET="${PROJECT_DIR}/rust/target/release"
if [ ! -f "${BUILD_TARGET}/libaudio_query.so" ] || [ ! -f "${BUILD_TARGET}/audio-watchdog-rs" ]; then
    echo -e "${RED}Error: Rust compilation failed to generate expected binaries.${NC}" >&2
    exit 1
fi
echo -e "${GREEN}  ✓ Rust modules compiled successfully.${NC}"

echo -e "\n${BLUE}[3/7] Installing application files...${NC}"
cp -f "${PROJECT_DIR}/audio_backend.py" "${INSTALL_DIR}/"
cp -f "${BUILD_TARGET}/libaudio_query.so" "${INSTALL_DIR}/"
cp -f "${BUILD_TARGET}/audio-watchdog-rs" "${BIN_DIR}/"
if [ -f "${BUILD_TARGET}/audio-query-rs" ]; then
    cp -f "${BUILD_TARGET}/audio-query-rs" "${BIN_DIR}/"
fi
cp -f "${PROJECT_DIR}/bin/macbook-audio-control" "${BIN_DIR}/macbook-audio-control"
chmod +x "${BIN_DIR}/macbook-audio-control"
chmod +x "${BIN_DIR}/audio-watchdog-rs"

# Initialize settings.json if not present
if [ ! -f "${INSTALL_DIR}/settings.json" ]; then
    echo '{"simultaneous_output": false, "smart_mic_auto_switch": false}' > "${INSTALL_DIR}/settings.json"
fi

echo -e "${GREEN}  ✓ Binaries and backend installed to ${INSTALL_DIR} and ${BIN_DIR}.${NC}"

echo -e "\n${BLUE}[4/7] Installing CS4208 3.5mm Headset Microphone (Pin 0x18) ALSA patch...${NC}"
if command -v sudo &>/dev/null; then
    sudo cp -f "${PROJECT_DIR}/configs/alsa/macbook-cs4208-mic.fw" /usr/lib/firmware/macbook-cs4208-mic.fw
    sudo cp -f "${PROJECT_DIR}/configs/alsa/50-macbook-cs4208-mic.conf" /etc/modprobe.d/50-macbook-cs4208-mic.conf
    sudo cp -f "${PROJECT_DIR}/configs/systemd/macbook-cs4208-mic.service" /etc/systemd/system/macbook-cs4208-mic.service
    sudo systemctl daemon-reload
    sudo systemctl enable macbook-cs4208-mic.service &>/dev/null || true

    # Apply live runtime pin override if Node 0x18 is not yet enabled
    if [ -d "/sys/class/sound/card1/hwC1D0" ] && ! amixer -c 1 scontrols 2>/dev/null | grep -q "'Internal Mic'"; then
        echo "0x18 0x00ab9150" | sudo tee /sys/class/sound/card1/hwC1D0/user_pin_configs >/dev/null
        systemctl --user stop pipewire.socket wireplumber pipewire 2>/dev/null || true
        echo 1 | sudo tee /sys/class/sound/card1/hwC1D0/reconfig >/dev/null || true
        systemctl --user start pipewire wireplumber 2>/dev/null || true
        sleep 1
    fi
    echo -e "${GREEN}  ✓ CS4208 Pin 0x18 (3.5mm Headset Mic) firmware patch & boot service installed.${NC}"
else
    echo -e "${YELLOW}  ! Warning: sudo not available; skipping /usr/lib/firmware & /etc/modprobe.d install.${NC}"
fi

echo -e "\n${BLUE}[5/7] Installing desktop launcher & systemd service...${NC}"
cp -f "${PROJECT_DIR}/macbook-audio-control.desktop" "${DESKTOP_DIR}/"
sed -i "s|^Exec=.*|Exec=${BIN_DIR}/macbook-audio-control|g" "${DESKTOP_DIR}/macbook-audio-control.desktop"
if command -v sudo &>/dev/null; then
    sudo ln -sf "${BIN_DIR}/macbook-audio-control" /usr/local/bin/macbook-audio-control 2>/dev/null || true
fi

if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "${DESKTOP_DIR}" &>/dev/null || true
fi

cp -f "${PROJECT_DIR}/configs/systemd/macbook-audio-watchdog.service" "${SYSTEMD_DIR}/"
systemctl --user daemon-reload
systemctl --user enable macbook-audio-watchdog.service &>/dev/null || true
systemctl --user restart macbook-audio-watchdog.service

echo -e "${GREEN}  ✓ Desktop entry and systemd background service configured.${NC}"

echo -e "\n${BLUE}[6/7] Deploying PipeWire & WirePlumber configuration drops...${NC}"
cp -f "${PROJECT_DIR}/configs/wireplumber/51-pro-audio-fix.conf" "${WIREPLUMBER_CONF_DIR}/"
cp -f "${PROJECT_DIR}/configs/pipewire/10-quantum.conf" "${PIPEWIRE_CONF_DIR}/"
echo -e "${GREEN}  ✓ Configuration drops deployed.${NC}"

echo -e "\n${BLUE}[7/7] Running system verification tests...${NC}"
python3 "${INSTALL_DIR}/audio_backend.py" --test

echo -e "\n${GREEN}======================================================================${NC}"
echo -e "${GREEN}       🎉 MacBook Audio Control Center successfully installed!       ${NC}"
echo -e "${GREEN}======================================================================${NC}"
echo -e "You can launch the application by:"
echo -e "  1. Running: ${YELLOW}macbook-audio-control${NC} in your terminal."
echo -e "  2. Searching for ${YELLOW}MacBook Audio Control${NC} in your app launcher (Rofi/Wofi/KRunner)."
echo -e "\nStatus of background watchdog daemon:"
"${BIN_DIR}/audio-watchdog-rs" --status
echo -e "Enjoy glitch-free, perfectly routed audio on your MacBook! 🎧🔊\n"
