"""
audio_backend.py - Core Audio Management Engine & GUI for PipeWire, WirePlumber, ALSA & Bluetooth
Optimized for CachyOS / Arch Linux with Hyprland (Wayland) on MacBook Broadwell-U (Cirrus Logic CS4208).
"""

import ctypes
import json
import os
import re
import sys
import shutil
import threading
import subprocess
from typing import Dict, List, Optional, Tuple, Any

# Configuration paths
PIPEWIRE_CONF_DIR = os.path.expanduser("~/.config/pipewire/pipewire.conf.d")
WIREPLUMBER_CONF_DIR = os.path.expanduser("~/.config/wireplumber/wireplumber.conf.d")

PIPEWIRE_QUANTUM_CONF = os.path.join(PIPEWIRE_CONF_DIR, "10-quantum.conf")
WIREPLUMBER_PRO_CONF = os.path.join(WIREPLUMBER_CONF_DIR, "51-pro-audio-fix.conf")
WIREPLUMBER_BT_CONF = os.path.join(WIREPLUMBER_CONF_DIR, "52-bluez-bitrate.conf")
APP_SETTINGS_FILE = os.path.expanduser("~/.local/share/macbook-audio-control/settings.json")

# Rust acceleration engine
LIB_AUDIO_QUERY_PATH = os.path.expanduser("~/.local/share/macbook-audio-control/libaudio_query.so")
_lib_audio_query = None
try:
    if os.path.exists(LIB_AUDIO_QUERY_PATH):
        _lib_audio_query = ctypes.CDLL(LIB_AUDIO_QUERY_PATH)
except Exception:
    _lib_audio_query = None



def run_cmd(cmd: List[str], timeout: float = 4.0) -> Tuple[int, str, str]:
    """Execute a command safely with timeout and return (code, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError:
        return -2, "", f"Executable not found: {cmd[0]}"
    except Exception as e:
        return -3, "", str(e)


# =============================================================================
# AUDIO BACKEND CONTROLLER
# =============================================================================

class AudioBackend:
    """Provides complete status inspection and hardware/software control for Audio."""

    def __init__(self):
        os.makedirs(PIPEWIRE_CONF_DIR, exist_ok=True)
        os.makedirs(WIREPLUMBER_CONF_DIR, exist_ok=True)

    # -------------------------------------------------------------------------
    # 1. PipeWire Quantum, Sample Rate, and Bitrate Calculations
    # -------------------------------------------------------------------------
    @staticmethod
    def calculate_pcm_bitrate(rate: int, bit_depth: int = 24, channels: int = 2) -> Tuple[float, str]:
        """
        Calculate uncompressed PCM audio bitrate in kilobits per second (kbps).
        Bitrate = (Sample Rate * Bit Depth * Channels) / 1000.
        """
        kbps = (rate * bit_depth * channels) / 1000.0
        if kbps >= 1000.0:
            mbps = kbps / 1000.0
            return kbps, f"{kbps:,.0f} kbps ({mbps:.2f} Mbps)"
        return kbps, f"{kbps:,.0f} kbps"

    @staticmethod
    def calculate_latency_ms(quantum: int, rate: int) -> float:
        """Calculate audio round-trip buffer latency in milliseconds."""
        if rate <= 0:
            return 0.0
        return (quantum / float(rate)) * 1000.0

    def get_pipewire_metadata_settings(self) -> Dict[str, Any]:
        """
        Query live PipeWire settings metadata via `pw-metadata -n settings 0`.
        """
        ret, stdout, _ = run_cmd(["pw-metadata", "-n", "settings", "0"])
        settings = {
            "clock.quantum": 1024,
            "clock.rate": 48000,
            "clock.force-quantum": 0,
            "clock.force-rate": 0,
            "clock.min-quantum": 32,
            "clock.max-quantum": 4096,
        }
        if ret == 0 and stdout:
            for line in stdout.splitlines():
                m = re.search(r"key:'([^']+)'\s+value:'([^']*)'", line)
                if m:
                    key, val = m.group(1), m.group(2)
                    try:
                        if val.isdigit():
                            settings[key] = int(val)
                        else:
                            settings[key] = val
                    except ValueError:
                        settings[key] = val
        return settings

    def get_saved_pipewire_config(self) -> Dict[str, Any]:
        """Read ~/.config/pipewire/pipewire.conf.d/10-quantum.conf."""
        result = {
            "rate": 48000,
            "quantum": 1024,
            "min_quantum": 32,
            "max_quantum": 4096,
            "bit_depth": 24,
            "allowed_rates": [44100, 48000, 88200, 96000, 176400, 192000],
            "exists": os.path.exists(PIPEWIRE_QUANTUM_CONF),
        }
        if os.path.exists(PIPEWIRE_QUANTUM_CONF):
            try:
                with open(PIPEWIRE_QUANTUM_CONF, "r", encoding="utf-8") as f:
                    content = f.read()

                m_q = re.search(r"default\.clock\.quantum\s*=\s*(\d+)", content)
                m_r = re.search(r"default\.clock\.rate\s*=\s*(\d+)", content)
                m_min = re.search(r"default\.clock\.min-quantum\s*=\s*(\d+)", content)
                m_max = re.search(r"default\.clock\.max-quantum\s*=\s*(\d+)", content)
                m_depth = re.search(r"audio\.format\s*=\s*\"?([A-Za-z0-9_]+)\"?", content)

                if m_q:
                    result["quantum"] = int(m_q.group(1))
                if m_r:
                    result["rate"] = int(m_r.group(1))
                if m_min:
                    result["min_quantum"] = int(m_min.group(1))
                if m_max:
                    result["max_quantum"] = int(m_max.group(1))
                if m_depth:
                    fmt = m_depth.group(1).upper()
                    if "16" in fmt:
                        result["bit_depth"] = 16
                    elif "24" in fmt:
                        result["bit_depth"] = 24
                    elif "32" in fmt:
                        result["bit_depth"] = 32
            except Exception as e:
                result["error"] = str(e)
        return result

    def get_saved_wireplumber_pro_fix(self) -> Dict[str, Any]:
        """Read ~/.config/wireplumber/wireplumber.conf.d/51-pro-audio-fix.conf."""
        result = {
            "period_size": 1024,
            "headroom": 1024,
            "priority_driver": 3000,
            "enabled": True,
            "exists": os.path.exists(WIREPLUMBER_PRO_CONF),
        }
        if os.path.exists(WIREPLUMBER_PRO_CONF):
            try:
                with open(WIREPLUMBER_PRO_CONF, "r", encoding="utf-8") as f:
                    content = f.read()
                m_p = re.search(r"api\.alsa\.period-size\s*=\s*(\d+)", content)
                m_h = re.search(r"api\.alsa\.headroom\s*=\s*(\d+)", content)
                m_pr = re.search(r"priority\.driver\s*=\s*(\d+)", content)

                if m_p:
                    result["period_size"] = int(m_p.group(1))
                if m_h:
                    result["headroom"] = int(m_h.group(1))
                if m_pr:
                    result["priority_driver"] = int(m_pr.group(1))
                if content.strip().startswith("# DISABLED"):
                    result["enabled"] = False
            except Exception as e:
                result["error"] = str(e)
        else:
            result["enabled"] = False
        return result

    def get_saved_wireplumber_bt_config(self) -> Dict[str, Any]:
        """Read ~/.config/wireplumber/wireplumber.conf.d/52-bluez-bitrate.conf."""
        result = {
            "ldac_quality": "hq",
            "default_rate": 48000,
            "enabled": True,
            "exists": os.path.exists(WIREPLUMBER_BT_CONF),
        }
        if os.path.exists(WIREPLUMBER_BT_CONF):
            try:
                with open(WIREPLUMBER_BT_CONF, "r", encoding="utf-8") as f:
                    content = f.read()
                m_q = re.search(r"bluez5\.a2dp\.ldac\.quality\s*=\s*\"?([a-z]+)\"?", content)
                m_r = re.search(r"bluez5\.default\.rate\s*=\s*(\d+)", content)
                if m_q:
                    result["ldac_quality"] = m_q.group(1)
                if m_r:
                    result["default_rate"] = int(m_r.group(1))
                if content.strip().startswith("# DISABLED"):
                    result["enabled"] = False
            except Exception as e:
                result["error"] = str(e)
        else:
            result["enabled"] = False
        return result

    def set_live_quantum(self, quantum: int) -> Tuple[bool, str]:
        """Dynamically force buffer size / quantum via pw-metadata."""
        code, out, err = run_cmd(["pw-metadata", "-n", "settings", "0", "clock.force-quantum", str(quantum)])
        if code == 0:
            return True, f"Live quantum set to {quantum} (0 = auto negotiation)"
        return False, f"Failed to set live quantum: {err or out}"

    def set_live_sample_rate(self, rate: int) -> Tuple[bool, str]:
        """Dynamically force sample clock rate via pw-metadata."""
        code, out, err = run_cmd(["pw-metadata", "-n", "settings", "0", "clock.force-rate", str(rate)])
        if code == 0:
            return True, f"Live sample rate set to {rate} Hz (0 = auto negotiation)"
        return False, f"Failed to set live sample rate: {err or out}"

    def save_pipewire_quantum_config(
        self,
        quantum: int,
        rate: int = 48000,
        bit_depth: int = 24,
        min_quantum: Optional[int] = None,
        max_quantum: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Persist clock properties, allowed rates, and bit depth to 10-quantum.conf."""
        if min_quantum is None:
            min_quantum = max(32, quantum // 2)
        if max_quantum is None:
            max_quantum = max(2048, quantum * 2)

        fmt_str = "S24LE"
        if bit_depth == 16:
            fmt_str = "S16LE"
        elif bit_depth == 32:
            fmt_str = "S32LE"

        content = f"""# Generated by MacBook Audio Control Center
context.properties = {{
    default.clock.rate          = {rate}
    default.clock.allowed-rates = [ 44100 48000 88200 96000 176400 192000 ]
    default.clock.quantum       = {quantum}
    default.clock.min-quantum   = {min_quantum}
    default.clock.max-quantum   = {max_quantum}
}}

context.modules = [
    {{
        name = libpipewire-module-adapter
        args = {{
            audio.format = "{fmt_str}"
        }}
    }}
]
"""
        try:
            if os.path.exists(PIPEWIRE_QUANTUM_CONF):
                shutil.copy2(PIPEWIRE_QUANTUM_CONF, PIPEWIRE_QUANTUM_CONF + ".bak")
            with open(PIPEWIRE_QUANTUM_CONF, "w", encoding="utf-8") as f:
                f.write(content)
            return True, f"Saved quantum {quantum}, rate {rate}Hz, and {bit_depth}-bit depth to {PIPEWIRE_QUANTUM_CONF}"
        except Exception as e:
            return False, f"Failed to save PipeWire configuration: {e}"

    def save_wireplumber_pro_fix(
        self,
        period_size: int = 1024,
        headroom: int = 1024,
        priority_driver: int = 3000,
        enabled: bool = True
    ) -> Tuple[bool, str]:
        """Persist WirePlumber ALSA pro-audio buffer rule."""
        prefix = "" if enabled else "# DISABLED by MacBook Audio Control Center\n# "
        content = f"""{prefix}# Override ALSA buffer, headroom, and driver priority for Pro Audio devices
{prefix}monitor.alsa.rules = [
{prefix}  {{
{prefix}    matches = [
{prefix}      {{
{prefix}        node.name = "~alsa_output.pci.*pro.*"
{prefix}      }}
{prefix}    ]
{prefix}    actions = {{
{prefix}      update-props = {{
{prefix}        api.alsa.period-size   = {period_size}
{prefix}        api.alsa.headroom      = {headroom}
{prefix}        priority.driver        = {priority_driver}
{prefix}      }}
{prefix}    }}
{prefix}  }},
{prefix}  {{
{prefix}    matches = [
{prefix}      {{
{prefix}        node.name = "~alsa_input.pci.*pro.*"
{prefix}      }}
{prefix}    ]
{prefix}    actions = {{
{prefix}      update-props = {{
{prefix}        api.alsa.period-size   = {period_size}
{prefix}        api.alsa.headroom      = {headroom}
{prefix}        priority.driver        = {max(1000, priority_driver - 1000)}
{prefix}      }}
{prefix}    }}
{prefix}  }}
{prefix}]
"""
        try:
            if os.path.exists(WIREPLUMBER_PRO_CONF):
                shutil.copy2(WIREPLUMBER_PRO_CONF, WIREPLUMBER_PRO_CONF + ".bak")
            with open(WIREPLUMBER_PRO_CONF, "w", encoding="utf-8") as f:
                f.write(content)
            return True, f"Saved WirePlumber Pro-Audio fix (period={period_size}, headroom={headroom}, enabled={enabled})"
        except Exception as e:
            return False, f"Failed to write WirePlumber configuration: {e}"

    def save_wireplumber_bt_config(
        self,
        ldac_quality: str = "hq",
        default_rate: int = 48000,
        enabled: bool = True
    ) -> Tuple[bool, str]:
        """Persist WirePlumber BlueZ Bluetooth A2DP codec bitrate & quality rule."""
        prefix = "" if enabled else "# DISABLED by MacBook Audio Control Center\n# "
        content = f"""{prefix}# BlueZ Bluetooth Bitrate & Codec Optimization
{prefix}monitor.bluez.rules = [
{prefix}  {{
{prefix}    matches = [
{prefix}      {{
{prefix}        device.name = "~bluez_card.*"
{prefix}      }}
{prefix}    ]
{prefix}    actions = {{
{prefix}      update-props = {{
{prefix}        bluez5.a2dp.ldac.quality = "{ldac_quality}"
{prefix}        bluez5.default.rate = {default_rate}
{prefix}        bluez5.enable-sbc-xq = true
{prefix}      }}
{prefix}    }}
{prefix}  }}
{prefix}]
"""
        try:
            if os.path.exists(WIREPLUMBER_BT_CONF):
                shutil.copy2(WIREPLUMBER_BT_CONF, WIREPLUMBER_BT_CONF + ".bak")
            with open(WIREPLUMBER_BT_CONF, "w", encoding="utf-8") as f:
                f.write(content)
            return True, f"Saved WirePlumber Bluetooth bitrate rule (quality={ldac_quality}, rate={default_rate}Hz)"
        except Exception as e:
            return False, f"Failed to write Bluetooth configuration: {e}"

    # -------------------------------------------------------------------------
    # 2. Audio Cards and Profiles (`pactl` / `wpctl`)
    # -------------------------------------------------------------------------
    def get_cards(self) -> List[Dict[str, Any]]:
        """
        Retrieve list of all audio cards, their active profiles, and available profiles.
        Disambiguates Cirrus Logic CS4208, Broadwell HDMI, and Bluetooth BlueZ cards.
        """
        cards: List[Dict[str, Any]] = []
        code, stdout, _ = run_cmd(["pactl", "list", "cards"])
        if code != 0 or not stdout:
            return self._get_cards_fallback()

        current_card: Optional[Dict[str, Any]] = None
        in_profiles = False

        for line in stdout.splitlines():
            line_str = line.strip()
            if line.startswith("Card #"):
                if current_card:
                    cards.append(current_card)
                card_num = line.split("#")[1].strip()
                current_card = {
                    "index": int(card_num) if card_num.isdigit() else card_num,
                    "name": "",
                    "description": "",
                    "driver": "",
                    "active_profile": "",
                    "profiles": [],
                }
                in_profiles = False
            elif current_card is not None:
                if line_str.startswith("Name:"):
                    current_card["name"] = line_str.split(":", 1)[1].strip()
                elif line_str.startswith("Driver:"):
                    current_card["driver"] = line_str.split(":", 1)[1].strip()
                elif line_str.startswith("alsa.mixer_name = "):
                    mixer = line_str.split("=", 1)[1].strip().strip('"')
                    current_card["mixer_name"] = mixer
                elif line_str.startswith("alsa.card_name = "):
                    cname = line_str.split("=", 1)[1].strip().strip('"')
                    current_card["card_name"] = cname
                elif line_str.startswith("device.description = "):
                    desc = line_str.split("=", 1)[1].strip().strip('"')
                    current_card["raw_description"] = desc
                elif line_str == "Profiles:":
                    in_profiles = True
                elif line_str.startswith("Active Profile:"):
                    in_profiles = False
                    current_card["active_profile"] = line_str.split(":", 1)[1].strip()
                elif in_profiles:
                    m = re.match(r"^([a-zA-Z0-9_\-+:]+):\s*(.*?)(?:\s*\((.*?)\))?$", line_str)
                    if m:
                        p_name = m.group(1)
                        p_desc = m.group(2)
                        p_details = m.group(3) or ""
                        available = "available: no" not in p_details
                        current_card["profiles"].append({
                            "name": p_name,
                            "description": p_desc or p_name,
                            "details": p_details,
                            "available": available,
                        })

        if current_card:
            cards.append(current_card)

        # Distinguish card descriptions cleanly
        for c in cards:
            cname = c.get("name", "")
            mixer = c.get("mixer_name", "")
            card_title = c.get("card_name", "")
            raw_desc = c.get("raw_description", "")

            if "00_1b.0" in cname or "CS4208" in mixer or "PCH" in card_title:
                c["description"] = "MacBook Built-in Audio (Cirrus Logic CS4208)"
                c["category"] = "internal"
            elif "00_03.0" in cname or "HDMI" in card_title or "HDMI" in mixer:
                c["description"] = "Broadwell HDMI Audio (Intel Broadwell-U HDMI)"
                c["category"] = "hdmi"
            elif "bluez" in cname:
                c["description"] = f"Bluetooth Audio ({raw_desc or 'Device'})"
                c["category"] = "bluetooth"
            else:
                c["description"] = raw_desc or cname
                c["category"] = "other"

        return cards

    def _get_cards_fallback(self) -> List[Dict[str, Any]]:
        status = self.get_wpctl_status()
        cards = []
        for dev in status.get("devices", []):
            cards.append({
                "index": dev["id"],
                "name": dev["name"],
                "description": dev["name"],
                "driver": dev["type"],
                "active_profile": "unknown",
                "profiles": [
                    {"name": "pro-audio", "description": "Pro Audio", "available": True},
                    {"name": "output:analog-stereo+input:analog-stereo", "description": "Analog Stereo Duplex", "available": True},
                    {"name": "off", "description": "Off", "available": True},
                ],
            })
        return cards

    def set_card_profile(self, card_name_or_index: Any, profile_name: str) -> Tuple[bool, str]:
        """Switch audio profile for a card using pactl, falling back to wpctl."""
        code, out, err = run_cmd(["pactl", "set-card-profile", str(card_name_or_index), profile_name])
        if code == 0:
            if "00_1b.0" in str(card_name_or_index) or str(card_name_or_index) == "1":
                self.apply_audio_routing("1")
            return True, f"Profile switched to '{profile_name}' on card {card_name_or_index}"

        # If card identifier is an ID or pactl failed, try wpctl by resolving profile index
        cards = self.get_cards()
        target_card = None
        for c in cards:
            if str(c.get("index")) == str(card_name_or_index) or c.get("name") == str(card_name_or_index):
                target_card = c
                break

        if target_card:
            p_idx = None
            for idx, p in enumerate(target_card.get("profiles", [])):
                if p["name"] == profile_name:
                    p_idx = idx
                    break
            if p_idx is not None:
                c_idx = target_card.get("index")
                code2, out2, err2 = run_cmd(["wpctl", "set-profile", str(c_idx), str(p_idx)])
                if code2 == 0:
                    if "00_1b.0" in target_card.get("name", ""):
                        self.apply_audio_routing("1")
                    return True, f"Profile switched to '{profile_name}' via wpctl"

        return False, f"Failed to switch profile: {err or out}"

    # -------------------------------------------------------------------------
    # 3. HDMI Audio Control
    # -------------------------------------------------------------------------
    def get_hdmi_status(self) -> Dict[str, Any]:
        """Query state of Broadwell HDMI audio card."""
        cards = self.get_cards()
        for c in cards:
            if c.get("category") == "hdmi" or "00_03.0" in c.get("name", ""):
                return {
                    "card": c,
                    "active_profile": c.get("active_profile", "off"),
                    "enabled": c.get("active_profile", "off") != "off",
                }
        return {"card": None, "active_profile": "off", "enabled": False}

    def set_hdmi_enabled(self, enable: bool, mode: str = "stereo") -> Tuple[bool, str]:
        """Enable or disable Broadwell HDMI audio."""
        cards = self.get_cards()
        hdmi_card = None
        for c in cards:
            if c.get("category") == "hdmi" or "00_03.0" in c.get("name", ""):
                hdmi_card = c
                break

        if not hdmi_card:
            return False, "Broadwell HDMI audio card not found"

        target_profile = "off"
        if enable:
            if mode == "pro":
                target_profile = "pro-audio"
            else:
                target_profile = "output:hdmi-stereo"

        return self.set_card_profile(hdmi_card["name"], target_profile)

    # -------------------------------------------------------------------------
    # 4. Audio Sinks, Sources & wpctl Status
    # -------------------------------------------------------------------------
    def get_wpctl_status(self) -> Dict[str, Any]:
        """Parse `wpctl status` into structured dictionaries."""
        code, stdout, _ = run_cmd(["wpctl", "status"])
        result = {
            "devices": [],
            "sinks": [],
            "sources": [],
            "default_sink": None,
            "default_source": None,
            "raw": stdout,
        }
        if code != 0 or not stdout:
            return result

        current_section = ""
        for line in stdout.splitlines():
            if "Audio" in line and "Devices:" in line:
                current_section = "devices"
                continue
            elif "├─ Sinks:" in line or "Sinks:" in line:
                current_section = "sinks"
                continue
            elif "├─ Sources:" in line or "Sources:" in line:
                current_section = "sources"
                continue
            elif any(marker in line for marker in ["Filters:", "Streams:", "Video", "Settings"]):
                current_section = ""
                continue

            if not current_section:
                continue

            m = re.search(r"(\*?)\s*(\d+)\.\s+([^\[]+)(?:\[(.*?)\])?", line)
            if m:
                is_default = bool(m.group(1).strip() == "*")
                item_id = int(m.group(2))
                name = m.group(3).strip()
                extra = m.group(4).strip() if m.group(4) else ""

                if current_section == "devices":
                    result["devices"].append({
                        "id": item_id,
                        "name": name,
                        "type": extra or "alsa",
                    })
                elif current_section in ("sinks", "sources"):
                    vol = 1.0
                    muted = "MUTED" in extra.upper()
                    m_vol = re.search(r"vol:\s*([0-9.]+)", extra)
                    if m_vol:
                        vol = float(m_vol.group(1))

                    data = {
                        "id": item_id,
                        "name": name,
                        "volume": vol,
                        "muted": muted,
                        "is_default": is_default,
                        "extra": extra,
                    }
                    if current_section == "sinks":
                        result["sinks"].append(data)
                        if is_default:
                            result["default_sink"] = data
                    else:
                        result["sources"].append(data)
                        if is_default:
                            result["default_source"] = data

        return result

    def set_default_sink(self, sink_id: int) -> Tuple[bool, str]:
        """Set default audio output sink."""
        code, out, err = run_cmd(["wpctl", "set-default", str(sink_id)])
        if code == 0:
            return True, f"Default output set to ID {sink_id}"
        return False, f"Failed to set default sink: {err or out}"

    def set_default_source(self, source_id: int) -> Tuple[bool, str]:
        """Set default audio input source."""
        code, out, err = run_cmd(["wpctl", "set-default", str(source_id)])
        if code == 0:
            return True, f"Default input set to ID {source_id}"
        return False, f"Failed to set default source: {err or out}"

    def set_volume(self, node_id: int, volume: float) -> Tuple[bool, str]:
        """Set volume (0.0 - 1.5)."""
        vol_str = f"{max(0.0, min(1.5, volume)):.2f}"
        code, out, err = run_cmd(["wpctl", "set-volume", str(node_id), vol_str])
        if code == 0:
            return True, f"Volume set to {vol_str} on node {node_id}"
        return False, f"Failed to set volume: {err or out}"

    def set_mute(self, node_id: int, mute: bool) -> Tuple[bool, str]:
        """Set mute state (1=mute, 0=unmute)."""
        val = "1" if mute else "0"
        code, out, err = run_cmd(["wpctl", "set-mute", str(node_id), val])
        if code == 0:
            return True, f"Node {node_id} {'muted' if mute else 'unmuted'}"
        return False, f"Failed to set mute: {err or out}"

    # -------------------------------------------------------------------------
    # 5. ALSA Hardware Switches (Cirrus Logic CS4208 on Card 1)
    # -------------------------------------------------------------------------
    def get_alsa_hardware_controls(self, card_num: str = "1") -> Dict[str, Any]:
        """
        Query ALSA mixer controls for card 1 (Cirrus Logic CS4208).
        Uses high-speed Rust C-ABI acceleration if available (< 0.2ms),
        with transparent fallback to amixer scontents.
        """
        if _lib_audio_query is not None:
            try:
                buf = ctypes.create_string_buffer(16384)
                res = _lib_audio_query.audio_query_alsa_json(card_num.encode("utf-8"), buf, 16384)
                if res == 0:
                    data = json.loads(buf.value.decode("utf-8"))
                    if "error" not in data:
                        return {
                            "master_switch": data.get("master", {}).get("unmuted", True),
                            "master_volume": data.get("master", {}).get("volume", 100),
                            "speaker_switch": data.get("speaker", {}).get("unmuted", True),
                            "speaker_volume": data.get("speaker", {}).get("volume", 100),
                            "headphone_switch": data.get("headphone", {}).get("unmuted", True),
                            "headphone_volume": data.get("headphone", {}).get("volume", 100),
                            "auto_mute_mode": data.get("auto_mute", "Disabled"),
                            "mic_boost": data.get("mic_boost", {}).get("volume", 3),
                            "internal_mic_boost": data.get("internal_mic_boost", {}).get("volume", 2),
                            "capture_switch": data.get("capture", {}).get("unmuted", True),
                            "capture_volume": data.get("capture", {}).get("volume", 81),
                            "capture_source": data.get("capture_source", "Internal Mic"),
                            "headset_mic_available": data.get("headset_mic_available", False),
                            "headphone_jack_plugged": self.get_headphone_jack_state(card_num),
                            "simultaneous_output": self.get_simultaneous_output(),
                            "smart_mic_auto_switch": self.get_smart_mic_auto_switch(),
                            "available": True,
                            "accelerated": True,
                        }
            except Exception:
                pass

        result = {
            "master_switch": True,
            "master_volume": 100,
            "speaker_switch": True,
            "speaker_volume": 100,
            "headphone_switch": True,
            "headphone_volume": 100,
            "auto_mute_mode": "Disabled",
            "mic_boost": 3,
            "internal_mic_boost": 2,
            "capture_switch": True,
            "capture_volume": 81,
            "capture_source": "Internal Mic",
            "headset_mic_available": False,
            "available": False,
            "accelerated": False,
        }
        code, stdout, _ = run_cmd(["amixer", "-c", card_num, "scontents"])
        if code != 0 or not stdout:
            return result

        result["available"] = True
        mic_cap_on = False

        # Parse each control block independently without DOTALL bleed
        blocks = stdout.split("Simple mixer control ")
        for block in blocks:
            if not block.strip():
                continue
            ctrl_name_match = re.match(r"'([^']+)',(\d+)", block)
            if not ctrl_name_match:
                continue
            name = ctrl_name_match.group(1)

            sw_match = re.search(r"\[(on|off)\]", block)
            vol_match = re.search(r"\[(\d+)%\]", block)

            if name == "Master":
                if sw_match:
                    result["master_switch"] = (sw_match.group(1).lower() == "on")
                if vol_match:
                    result["master_volume"] = int(vol_match.group(1))
            elif name == "Speaker":
                if sw_match:
                    result["speaker_switch"] = (sw_match.group(1).lower() == "on")
                if vol_match:
                    result["speaker_volume"] = int(vol_match.group(1))
            elif name == "Headphone":
                if sw_match:
                    result["headphone_switch"] = (sw_match.group(1).lower() == "on")
                if vol_match:
                    result["headphone_volume"] = int(vol_match.group(1))
            elif name == "Auto-Mute Mode":
                m_item = re.search(r"Item0:\s*'([^']+)'", block)
                if m_item:
                    result["auto_mute_mode"] = m_item.group(1)
            elif name == "Capture":
                if sw_match:
                    result["capture_switch"] = (sw_match.group(1).lower() == "on")
                if vol_match:
                    result["capture_volume"] = int(vol_match.group(1))
            elif name == "Mic":
                if sw_match and sw_match.group(1).lower() == "on":
                    mic_cap_on = True
            elif name == "Internal Mic":
                result["headset_mic_available"] = True
            elif name == "Mic Boost":
                m_val = re.search(r"Front Left:\s*(\d+)", block)
                if m_val:
                    result["mic_boost"] = int(m_val.group(1))
            elif name == "Internal Mic Boost":
                m_val = re.search(r"Front Left:\s*(\d+)", block)
                if m_val:
                    result["internal_mic_boost"] = int(m_val.group(1))

        if result["headset_mic_available"] and mic_cap_on:
            result["capture_source"] = "Mic"
        else:
            result["capture_source"] = "Internal Mic"

        result["headphone_jack_plugged"] = self.get_headphone_jack_state(card_num)
        result["simultaneous_output"] = self.get_simultaneous_output()
        result["smart_mic_auto_switch"] = self.get_smart_mic_auto_switch()
        return result

    def set_alsa_switch(self, control_name: str, enable: bool, card_num: str = "1") -> Tuple[bool, str]:
        """Turn ALSA control on or off (e.g. 'Speaker', 'Headphone', 'Master', 'Capture'). Uses Rust fast-path if available."""
        # If unmuting Speaker or Headphone whose volume was zeroed by PipeWire profile switch, restore volume to 100%
        if enable and control_name in ("Speaker", "Headphone"):
            alsa_cur = self.get_alsa_hardware_controls(card_num)
            vol_key = "speaker_volume" if control_name == "Speaker" else "headphone_volume"
            if alsa_cur.get(vol_key, 100) == 0:
                self.set_alsa_volume(control_name, 100, card_num)

        if _lib_audio_query is not None:
            try:
                res = _lib_audio_query.audio_set_alsa_switch(
                    card_num.encode("utf-8"),
                    control_name.encode("utf-8"),
                    1 if enable else 0
                )
                if res == 0:
                    state = "unmute" if enable else "mute"
                    return True, f"ALSA {control_name} on card {card_num} set to {state} (Rust fast-path)"
            except Exception:
                pass

        if control_name in ("Capture", "Mic", "Internal Mic"):
            state = "cap" if enable else "nocap"
        else:
            state = "unmute" if enable else "mute"
        code, out, err = run_cmd(["amixer", "-c", card_num, "sset", control_name, state])
        if code == 0:
            return True, f"ALSA {control_name} on card {card_num} set to {state}"
        return False, f"Failed to set ALSA {control_name}: {err or out}"

    def set_alsa_volume(self, control_name: str, percent: int, card_num: str = "1") -> Tuple[bool, str]:
        """Set ALSA volume percent (e.g. 'Speaker', 'Headphone', 'Master'). Uses Rust fast-path if available."""
        clamped = max(0, min(100, int(percent)))
        if _lib_audio_query is not None:
            try:
                res = _lib_audio_query.audio_set_alsa_volume(
                    card_num.encode("utf-8"),
                    control_name.encode("utf-8"),
                    clamped
                )
                if res == 0:
                    return True, f"ALSA {control_name} on card {card_num} set to {clamped}% (Rust fast-path)"
            except Exception:
                pass

        val_str = f"{clamped}%"
        code, out, err = run_cmd(["amixer", "-c", card_num, "set", control_name, val_str])
        if code == 0:
            return True, f"ALSA {control_name} on card {card_num} set to {val_str}"
        return False, f"Failed to set ALSA volume: {err or out}"

    def set_alsa_auto_mute(self, enable: bool, card_num: str = "1") -> Tuple[bool, str]:
        """Set ALSA Auto-Mute Mode ('Enabled' or 'Disabled'). Uses Rust fast-path if available."""
        if _lib_audio_query is not None:
            try:
                res = _lib_audio_query.audio_set_alsa_auto_mute(
                    card_num.encode("utf-8"),
                    1 if enable else 0
                )
                if res == 0:
                    mode_str = "Enabled" if enable else "Disabled"
                    return True, f"ALSA Auto-Mute Mode set to {mode_str} (Rust fast-path)"
            except Exception:
                pass

        mode_str = "Enabled" if enable else "Disabled"
        code, out, err = run_cmd(["amixer", "-c", card_num, "set", "Auto-Mute Mode", mode_str])
        if code == 0:
            return True, f"ALSA Auto-Mute Mode set to {mode_str}"
        return False, f"Failed to set Auto-Mute Mode: {err or out}"

    def set_alsa_boost(self, control_name: str, level: int, card_num: str = "1") -> Tuple[bool, str]:
        """Set ALSA Mic Boost level (e.g. 'Mic Boost' 0-3, 'Internal Mic Boost' 0-2). Uses Rust fast-path if available."""
        if _lib_audio_query is not None and hasattr(_lib_audio_query, "audio_set_alsa_raw_volume"):
            try:
                res = _lib_audio_query.audio_set_alsa_raw_volume(
                    card_num.encode("utf-8"),
                    control_name.encode("utf-8"),
                    int(level)
                )
                if res == 0:
                    return True, f"ALSA {control_name} level set to {level} (Rust fast-path)"
            except Exception:
                pass

        code, out, err = run_cmd(["amixer", "-c", card_num, "sset", control_name, str(level)])
        if code == 0:
            return True, f"ALSA {control_name} level set to {level}"
        return False, f"Failed to set ALSA boost: {err or out}"

    def ensure_headset_mic_pin_enabled(self, card_num: str = "1") -> Tuple[bool, str]:
        """
        Ensure Cirrus Logic CS4208 Node 0x18 (3.5mm External Headset Mic) is enabled in ALSA.
        Kernel driver snd_hda_codec_cs420x (mba6_pincfgs) marks 0x18 as 0x43ab9030 (disconnected).
        Overriding 0x18 to 0x00ab9150 enables both 'Mic' (0x18) and 'Internal Mic' (0x1c).
        """
        code, stdout, _ = run_cmd(["amixer", "-c", card_num, "scontrols"])
        if code == 0 and "'Internal Mic'" in stdout:
            return True, "CS4208 Pin 0x18 (3.5mm Headset Mic) is enabled"

        sysfs_dir = f"/sys/class/sound/card{card_num}/hwC{card_num}D0"
        if not os.path.exists(sysfs_dir):
            return False, f"HDA sysfs node {sysfs_dir} not found"

        cmd_str = (
            f"echo '0x18 0x00ab9150' | sudo -n tee {sysfs_dir}/user_pin_configs >/dev/null && "
            f"systemctl --user stop pipewire.socket wireplumber pipewire 2>/dev/null || true; "
            f"echo 1 | sudo -n tee {sysfs_dir}/reconfig >/dev/null; "
            f"systemctl --user start pipewire wireplumber 2>/dev/null || true"
        )
        code2, out2, err2 = run_cmd(["/bin/sh", "-c", cmd_str], timeout=10.0)
        code3, stdout3, _ = run_cmd(["amixer", "-c", card_num, "scontrols"])
        if code3 == 0 and "'Internal Mic'" in stdout3:
            return True, "CS4208 Pin 0x18 (3.5mm Headset Mic) dynamically enabled via sysfs reconfig"
        return False, f"Could not dynamically reconfigure Pin 0x18: {err2 or out2}"

    def get_capture_source(self, card_num: str = "1") -> str:
        """Return currently active ALSA capture source ('Mic' for 3.5mm Headset Mic, 'Internal Mic' for Built-in)."""
        alsa = self.get_alsa_hardware_controls(card_num)
        return alsa.get("capture_source", "Internal Mic")

    def set_capture_source(
        self,
        source: str,
        card_num: str = "1",
        adjust_gain: bool = True
    ) -> Tuple[bool, str]:
        """
        Switch active microphone input between:
        - 'Mic' (External 3.5mm TRRS Headset Microphone, CS4208 Node 0x18)
        - 'Internal Mic' (Built-in Digital Microphone, CS4208 Node 0x1c)
        Works across both Pro Audio and Analog Stereo Duplex PipeWire profiles.
        """
        want_headset = source.lower() in ("mic", "headset", "headset mic", "external", "0")
        target_alsa = "Mic" if want_headset else "Internal Mic"

        # 1. Switch via Rust C-ABI fast-path if available
        if _lib_audio_query is not None and hasattr(_lib_audio_query, "audio_set_alsa_capture_source"):
            try:
                _lib_audio_query.audio_set_alsa_capture_source(
                    card_num.encode("utf-8"),
                    target_alsa.encode("utf-8")
                )
            except Exception:
                pass

        # 2. Ensure both Simple Mixer cswitch-exclusive and raw Capture Source enum are synced
        run_cmd(["amixer", "-c", card_num, "sset", target_alsa, "cap"])
        enum_idx = "0" if want_headset else "1"
        run_cmd(["amixer", "-c", card_num, "cset", "name=Capture Source", enum_idx])

        # 3. Configure optimal analog/digital preamp gain if requested
        if adjust_gain:
            if want_headset:
                # Passive analog electret headset mic on Node 0x18 needs +10dB Mic Boost (3) and +12dB Capture (100%)
                self.set_alsa_boost("Mic Boost", 3, card_num=card_num)
                self.set_alsa_volume("Capture", 100, card_num=card_num)
                self.set_alsa_switch("Capture", True, card_num=card_num)
            else:
                # Amplified digital PDM internal mic on Node 0x1c works best at 0dB Boost (2) and 0dB Capture (81%)
                self.set_alsa_boost("Internal Mic Boost", 2, card_num=card_num)
                self.set_alsa_volume("Capture", 81, card_num=card_num)
                self.set_alsa_switch("Capture", True, card_num=card_num)

        # 4. Sync PipeWire port if card is currently using analog-stereo profile
        pw_port = "analog-input-mic" if want_headset else "analog-input-internal-mic"
        run_cmd(["pactl", "set-source-port", "alsa_input.pci-0000_00_1b.0.analog-stereo", pw_port])

        label = "3.5mm Headset Mic (Pin 0x18)" if want_headset else "Built-in Internal Mic (Pin 0x1c)"
        return True, f"Microphone input switched to {label}"

    # -------------------------------------------------------------------------
    # 5b. Settings & Smart Exclusive Headphone Jack Auto-Routing
    # -------------------------------------------------------------------------
    def load_settings(self) -> Dict[str, Any]:
        """Load user audio preferences from settings.json."""
        defaults = {
            "simultaneous_output": False,
            "smart_mic_auto_switch": True,
        }
        if os.path.exists(APP_SETTINGS_FILE):
            try:
                with open(APP_SETTINGS_FILE, "r") as f:
                    data = json.load(f)
                    defaults.update(data)
            except Exception:
                pass
        return defaults

    def save_settings(self, settings_dict: Dict[str, Any]) -> bool:
        """Persist user audio preferences to settings.json."""
        try:
            os.makedirs(os.path.dirname(APP_SETTINGS_FILE), exist_ok=True)
            with open(APP_SETTINGS_FILE, "w") as f:
                json.dump(settings_dict, f, indent=2)
            return True
        except Exception:
            return False

    def get_simultaneous_output(self) -> bool:
        """Check whether Simultaneous Output (Speakers + Headphones) is enabled (default False)."""
        return bool(self.load_settings().get("simultaneous_output", False))

    def set_simultaneous_output(self, enabled: bool) -> bool:
        """Toggle simultaneous output and immediately apply audio routing."""
        st = self.load_settings()
        st["simultaneous_output"] = bool(enabled)
        ok = self.save_settings(st)
        self.apply_audio_routing()
        return ok

    def get_smart_mic_auto_switch(self) -> bool:
        """Check whether Smart Microphone Auto-Switch on 3.5mm jack plug/unplug is enabled (default True)."""
        return bool(self.load_settings().get("smart_mic_auto_switch", True))

    def set_smart_mic_auto_switch(self, enabled: bool) -> bool:
        """Toggle smart microphone auto-switching and immediately apply routing."""
        st = self.load_settings()
        st["smart_mic_auto_switch"] = bool(enabled)
        ok = self.save_settings(st)
        if enabled:
            self.apply_audio_routing()
        return ok

    def get_headphone_jack_state(self, card_num: str = "1") -> bool:
        """Return True if 3.5mm headphone jack is plugged in on ALSA Card 1, False otherwise."""
        code, stdout, _ = run_cmd(["amixer", "-c", card_num, "cget", "iface=CARD,name='Headphone Jack'"])
        if code == 0 and stdout:
            if "values=on" in stdout:
                return True
            if "values=off" in stdout:
                return False
        # Fallback to numid=14
        code, stdout, _ = run_cmd(["amixer", "-c", card_num, "cget", "numid=14"])
        if code == 0 and stdout:
            if "values=on" in stdout:
                return True
            if "values=off" in stdout:
                return False
        return False

    def apply_audio_routing(self, card_num: str = "1") -> Tuple[bool, str]:
        """
        Apply exclusive auto-routing or simultaneous output plus smart microphone routing
        based on settings and 3.5mm headphone jack state.
        - Output Default: Exclusive Routing (Jack plugged -> Headphones only; unplugged -> Speakers only).
        - Output Simultaneous Mode: Both Speakers and Headphones unmuted.
        - Input Smart Auto-Switch (Default True): Jack plugged -> Headset Mic (0x18); unplugged -> Internal Mic (0x1c).
        """
        simultaneous = self.get_simultaneous_output()
        smart_mic = self.get_smart_mic_auto_switch()
        jack_plugged = self.get_headphone_jack_state(card_num=card_num)

        mic_msg = ""
        if smart_mic:
            target_mic = "Mic" if jack_plugged else "Internal Mic"
            _, m_msg = self.set_capture_source(target_mic, card_num=card_num, adjust_gain=True)
            mic_msg = f" | {m_msg}"

        if simultaneous:
            self.set_alsa_switch("Speaker", True, card_num=card_num)
            self.set_alsa_switch("Headphone", True, card_num=card_num)
            return True, f"Simultaneous Output: Speakers and Headphones both unmuted{mic_msg}"

        if jack_plugged:
            self.set_alsa_switch("Speaker", False, card_num=card_num)
            self.set_alsa_switch("Headphone", True, card_num=card_num)
            return True, f"Exclusive Routing: Headphones plugged in (Speakers muted, Headphones unmuted){mic_msg}"
        else:
            self.set_alsa_switch("Speaker", True, card_num=card_num)
            return True, f"Exclusive Routing: Headphones unplugged (Speakers unmuted){mic_msg}"

    # -------------------------------------------------------------------------
    # 6. Audio Watchdog Daemon Management (`audio-watchdog-rs`)
    # -------------------------------------------------------------------------
    def get_watchdog_status(self) -> Dict[str, Any]:
        """Check status of audio-watchdog-rs daemon."""
        status_path = os.path.expanduser("~/.local/share/macbook-audio-control/watchdog_status.json")
        if os.path.exists(status_path):
            try:
                with open(status_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"running": False, "xruns": 0, "last_xrun": "never"}

    def start_watchdog(self) -> Tuple[bool, str]:
        """Start audio-watchdog-rs in the background."""
        bin_path = os.path.expanduser("~/.local/bin/audio-watchdog-rs")
        if not os.path.exists(bin_path):
            return False, "audio-watchdog-rs binary not found"
        try:
            subprocess.Popen([bin_path, "--daemon"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, "Audio Watchdog daemon started"
        except Exception as e:
            return False, str(e)

    def stop_watchdog(self) -> Tuple[bool, str]:
        """Stop audio-watchdog-rs daemon."""
        bin_path = os.path.expanduser("~/.local/bin/audio-watchdog-rs")
        if not os.path.exists(bin_path):
            return False, "audio-watchdog-rs binary not found"
        code, out, err = run_cmd([bin_path, "--stop"])
        return code == 0, out or err

    # -------------------------------------------------------------------------
    # 7. Bluetooth Device Management (`bluetoothctl`)
    # -------------------------------------------------------------------------
    def get_bluetooth_devices(self) -> List[Dict[str, Any]]:
        """List all paired/connected Bluetooth devices and their audio status."""
        devices = []
        code, stdout, _ = run_cmd(["bluetoothctl", "devices"])
        if code != 0 or not stdout:
            return devices

        for line in stdout.splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) >= 3 and parts[0] == "Device":
                mac = parts[1]
                name = parts[2]
                dev_info = self._get_bluetooth_device_info(mac, name)
                devices.append(dev_info)

        return devices

    def _get_bluetooth_device_info(self, mac: str, name: str) -> Dict[str, Any]:
        info = {
            "mac": mac,
            "name": name,
            "connected": False,
            "paired": False,
            "trusted": False,
            "battery": None,
            "icon": "audio-card",
            "is_audio": False,
        }
        code, stdout, _ = run_cmd(["bluetoothctl", "info", mac])
        if code == 0 and stdout:
            if "Connected: yes" in stdout:
                info["connected"] = True
            if "Paired: yes" in stdout:
                info["paired"] = True
            if "Trusted: yes" in stdout:
                info["trusted"] = True

            m_bat = re.search(r"Battery Percentage:\s*0x[0-9a-fA-F]+\s*\((\d+)\)", stdout)
            if m_bat:
                info["battery"] = int(m_bat.group(1))

            m_icon = re.search(r"Icon:\s*([a-zA-Z0-9_\-]+)", stdout)
            if m_icon:
                info["icon"] = m_icon.group(1)

            if "Audio" in stdout or "audio" in info["icon"] or "headset" in info["icon"] or "headphone" in info["icon"]:
                info["is_audio"] = True
        return info

    def connect_bluetooth(self, mac: str) -> Tuple[bool, str]:
        """Connect to a Bluetooth device."""
        code, out, err = run_cmd(["bluetoothctl", "connect", mac], timeout=8.0)
        if code == 0 and ("Connection successful" in out or "connected" in out.lower()):
            return True, f"Successfully connected to {mac}"
        return False, f"Connection response: {out or err}"

    def disconnect_bluetooth(self, mac: str) -> Tuple[bool, str]:
        """Disconnect a Bluetooth device."""
        code, out, err = run_cmd(["bluetoothctl", "disconnect", mac], timeout=6.0)
        if code == 0 and ("Successful disconnected" in out or "Disconnection successful" in out or "disconnected" in out.lower()):
            return True, f"Disconnected {mac}"
        return False, f"Disconnection response: {out or err}"

    def get_bluetooth_card_for_mac(self, mac: str) -> Optional[Dict[str, Any]]:
        """Find PipeWire card associated with a Bluetooth MAC address."""
        formatted_mac = mac.replace(":", "_")
        cards = self.get_cards()
        for c in cards:
            if formatted_mac.lower() in c.get("name", "").lower():
                return c
        return None

    # -------------------------------------------------------------------------
    # 7. Service Control & Diagnostics
    # -------------------------------------------------------------------------
    def restart_pipewire_services(self) -> Tuple[bool, str]:
        """Restart user services pipewire, wireplumber, and pipewire-pulse."""
        code, out, err = run_cmd(
            ["systemctl", "--user", "restart", "pipewire", "wireplumber", "pipewire-pulse"],
            timeout=8.0
        )
        if code == 0:
            return True, "PipeWire and WirePlumber restarted successfully"
        return False, f"Failed to restart services: {err or out}"

    # -------------------------------------------------------------------------
    # 8. Preset Engine
    # -------------------------------------------------------------------------
    def get_presets(self) -> List[Dict[str, Any]]:
        """Return full suite of tailored audio presets."""
        return [
            {
                "id": "pro_audio_fix",
                "name": "Pro Audio Fix (Current Baseline)",
                "description": "Safe, jitter-free Pro Audio profile with 1024 buffer size, 48kHz (2.3 Mbps bitrate), Auto-Mute Disabled, and CS4208 period-size fix.",
                "profile": "pro-audio",
                "quantum": 1024,
                "rate": 48000,
                "bit_depth": 24,
                "period_size": 1024,
                "headroom": 1024,
                "wireplumber_enabled": True,
                "color": "#06d6a0",  # Green
            },
            {
                "id": "gaming_low_latency",
                "name": "Gaming / Low Latency (5.3ms)",
                "description": "256 quantum buffer size at 48kHz (2.3 Mbps bitrate). Ultra-low input-to-output latency for competitive gaming and voice chat.",
                "profile": "pro-audio",
                "quantum": 256,
                "rate": 48000,
                "bit_depth": 24,
                "period_size": 256,
                "headroom": 256,
                "wireplumber_enabled": True,
                "color": "#ef476f",  # Magenta / Red
            },
            {
                "id": "daw_ultra_low_latency",
                "name": "DAW / Musician Ultra-Fast (2.7ms)",
                "description": "128 quantum buffer size at 48kHz (2.3 Mbps bitrate). Minimum monitoring latency for live instrument tracking and MIDI synthesizers.",
                "profile": "pro-audio",
                "quantum": 128,
                "rate": 48000,
                "bit_depth": 24,
                "period_size": 128,
                "headroom": 128,
                "wireplumber_enabled": True,
                "color": "#ffbe0b",  # Yellow
            },
            {
                "id": "studio_master_96k",
                "name": "Studio High Sample Rate (96 kHz)",
                "description": "Studio recording standard at 96kHz with 24-bit depth (4.6 Mbps raw bitrate) and 1024 buffer. High frequency fidelity and dynamic range.",
                "profile": "pro-audio",
                "quantum": 1024,
                "rate": 96000,
                "bit_depth": 24,
                "period_size": 1024,
                "headroom": 1024,
                "wireplumber_enabled": True,
                "color": "#118ab2",  # Cyan
            },
            {
                "id": "audiophile_192k",
                "name": "Audiophile Hi-Res (192 kHz)",
                "description": "Maximum resolution 192kHz sample rate with 24-bit depth (9.2 Mbps raw bitrate) and 2048 buffer for pristine lossless playback.",
                "profile": "pro-audio",
                "quantum": 2048,
                "rate": 192000,
                "bit_depth": 24,
                "period_size": 2048,
                "headroom": 2048,
                "wireplumber_enabled": True,
                "color": "#8338ec",  # Purple
            },
            {
                "id": "daily_analog_duplex",
                "name": "Daily / Stable Analog Duplex",
                "description": "Standard consumer Analog Stereo Duplex profile. Traditional auto-switching jack routing with balanced 1024 buffer at 16-bit / 48kHz.",
                "profile": "output:analog-stereo+input:analog-stereo",
                "quantum": 1024,
                "rate": 48000,
                "bit_depth": 16,
                "period_size": 1024,
                "headroom": 1024,
                "wireplumber_enabled": False,
                "color": "#3a86ff",  # Soft Blue
            },
            {
                "id": "bluetooth_hq_a2dp",
                "name": "High-Fidelity Bluetooth A2DP",
                "description": "Optimized wireless audio profile: LDAC / SBC-XQ High Quality mode (up to 990 kbps) with 1024 buffer size.",
                "profile": "pro-audio",
                "quantum": 1024,
                "rate": 48000,
                "bit_depth": 24,
                "period_size": 1024,
                "headroom": 1024,
                "wireplumber_enabled": True,
                "color": "#00b4d8",  # Sky Blue
            },
        ]

    def apply_preset(self, preset_id: str, restart_service: bool = False) -> Tuple[bool, List[str]]:
        """Apply a preset across card profile, live quantum, live sample rate, and persistent configs."""
        logs = []
        matched = None
        for p in self.get_presets():
            if p["id"] == preset_id:
                matched = p
                break
        if not matched:
            return False, [f"Unknown preset ID: {preset_id}"]

        target_card = "alsa_card.pci-0000_00_1b.0"

        # 1. Card profile
        ok_prof, msg_prof = self.set_card_profile(target_card, matched["profile"])
        logs.append(msg_prof)

        # 2. Live metadata
        ok_lq, msg_lq = self.set_live_quantum(matched["quantum"])
        logs.append(msg_lq)
        ok_lr, msg_lr = self.set_live_sample_rate(matched["rate"])
        logs.append(msg_lr)

        # 3. Persistent PipeWire config
        ok_pw, msg_pw = self.save_pipewire_quantum_config(
            quantum=matched["quantum"],
            rate=matched["rate"],
            bit_depth=matched.get("bit_depth", 24),
            min_quantum=max(32, matched["quantum"] // 2),
            max_quantum=max(2048, matched["quantum"] * 2),
        )
        logs.append(msg_pw)

        # 4. WirePlumber Pro fix config
        ok_wp, msg_wp = self.save_wireplumber_pro_fix(
            period_size=matched["period_size"],
            headroom=matched["headroom"],
            priority_driver=3000,
            enabled=matched["wireplumber_enabled"],
        )
        logs.append(msg_wp)

        # 5. CS4208 ALSA controls & Intelligent Routing
        self.set_alsa_switch("Master", True, card_num="1")
        ok_route, msg_route = self.apply_audio_routing(card_num="1")
        logs.append(f"ALSA Routing: {msg_route}")

        # 6. Bluetooth specific rule if preset requested
        if preset_id == "bluetooth_hq_a2dp":
            self.save_wireplumber_bt_config(ldac_quality="hq", default_rate=48000, enabled=True)
            logs.append("Enabled WirePlumber BlueZ High-Bitrate rule (LDAC HQ 990k / SBC-XQ)")

        if restart_service:
            ok_rst, msg_rst = self.restart_pipewire_services()
            logs.append(msg_rst)

        return True, logs


# =============================================================================
# GUI IMPLEMENTATION (PyQt5 / PyQt6 with Wayland / Hyprland Support)
# =============================================================================

QT_AVAILABLE = False
QT_VERSION = 0

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QTabWidget, QLabel, QPushButton, QComboBox, QSlider, QCheckBox,
        QRadioButton, QButtonGroup, QGroupBox, QScrollArea,
        QFrame, QMessageBox, QTextEdit, QGridLayout, QListWidget, QListWidgetItem
    )
    QT_AVAILABLE = True
    QT_VERSION = 5
except ImportError:
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets
        from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
        from PyQt6.QtWidgets import (
            QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
            QTabWidget, QLabel, QPushButton, QComboBox, QSlider, QCheckBox,
            QRadioButton, QButtonGroup, QGroupBox, QScrollArea,
            QFrame, QMessageBox, QTextEdit, QGridLayout, QListWidget, QListWidgetItem
        )
        QT_AVAILABLE = True
        QT_VERSION = 6
    except ImportError:
        QT_AVAILABLE = False
        QT_VERSION = 0


DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1a1b26;
    color: #c0caf5;
    font-family: 'Inter', 'Noto Sans', 'Segoe UI', sans-serif;
    font-size: 13px;
}

QTabWidget::pane {
    border: 1px solid #292e42;
    background-color: #1a1b26;
    border-radius: 8px;
    padding: 6px;
}

QTabBar::tab {
    background-color: #24283b;
    color: #a9b1d6;
    padding: 10px 18px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: bold;
}

QTabBar::tab:selected {
    background-color: #7aa2f7;
    color: #15161e;
}

QTabBar::tab:hover:!selected {
    background-color: #414868;
    color: #ffffff;
}

QGroupBox {
    border: 1px solid #414868;
    border-radius: 8px;
    margin-top: 18px;
    padding: 16px 12px 12px 12px;
    font-weight: bold;
    color: #7aa2f7;
    background-color: #1f2335;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    background-color: #1f2335;
}

QPushButton {
    background-color: #24283b;
    color: #c0caf5;
    border: 1px solid #414868;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #414868;
    border-color: #7aa2f7;
    color: #ffffff;
}

QPushButton:pressed {
    background-color: #7aa2f7;
    color: #15161e;
}

QComboBox {
    background-color: #24283b;
    border: 1px solid #414868;
    border-radius: 6px;
    padding: 6px 12px;
    color: #c0caf5;
}

QComboBox:hover {
    border-color: #7aa2f7;
}

QComboBox QAbstractItemView {
    background-color: #24283b;
    border: 1px solid #414868;
    color: #c0caf5;
    selection-background-color: #7aa2f7;
    selection-color: #15161e;
}

QSlider::groove:horizontal {
    height: 8px;
    background: #24283b;
    border-radius: 4px;
}

QSlider::sub-page:horizontal {
    background: #7aa2f7;
    border-radius: 4px;
}

QSlider::handle:horizontal {
    background: #c0caf5;
    border: 1px solid #7aa2f7;
    width: 18px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 9px;
}

QSlider::handle:horizontal:hover {
    background: #ffffff;
    border-color: #06d6a0;
}

QRadioButton, QCheckBox {
    spacing: 8px;
    color: #c0caf5;
}

QRadioButton::indicator, QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #414868;
    background-color: #24283b;
}

QRadioButton::indicator:checked, QCheckBox::indicator:checked {
    background-color: #7aa2f7;
    border-color: #7aa2f7;
}

QRadioButton::indicator {
    border-radius: 9px;
}

QTextEdit {
    background-color: #16161e;
    border: 1px solid #292e42;
    border-radius: 6px;
    color: #a9b1d6;
    font-family: 'JetBrains Mono', 'Fira Code', 'Monospace', monospace;
    font-size: 12px;
}

QScrollArea {
    border: none;
    background: transparent;
}
"""


class PresetCardWidget(QFrame):
    """Clickable styled preset card widget."""
    applied = pyqtSignal(str)

    def __init__(self, preset_data: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.preset_id = preset_data["id"]
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: #24283b;
                border: 1px solid #414868;
                border-left: 6px solid {preset_data.get('color', '#7aa2f7')};
                border-radius: 8px;
                padding: 12px;
            }}
            QFrame:hover {{
                border-color: {preset_data.get('color', '#7aa2f7')};
                background-color: #292e42;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Header: Name and badges
        top_row = QHBoxLayout()
        name_label = QLabel(preset_data["name"])
        name_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #ffffff;")
        top_row.addWidget(name_label)
        top_row.addStretch()

        kbps, kbps_str = AudioBackend.calculate_pcm_bitrate(
            preset_data["rate"],
            preset_data.get("bit_depth", 24)
        )
        badge_txt = f"{preset_data['quantum']} smp @ {preset_data['rate']//1000}kHz • {kbps_str}"
        badge = QLabel(badge_txt)
        badge.setStyleSheet(f"""
            background-color: #1f2335;
            color: {preset_data.get('color', '#7aa2f7')};
            border: 1px solid {preset_data.get('color', '#7aa2f7')};
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 11px;
            font-weight: bold;
        """)
        top_row.addWidget(badge)
        layout.addLayout(top_row)

        # Description
        desc_label = QLabel(preset_data["description"])
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #a9b1d6; font-size: 12px;")
        layout.addWidget(desc_label)

        # Action button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        apply_btn = QPushButton("Apply Preset")
        apply_btn.setStyleSheet(f"""
            background-color: {preset_data.get('color', '#7aa2f7')};
            color: #15161e;
            font-weight: bold;
            padding: 6px 16px;
        """)
        apply_btn.clicked.connect(lambda: self.applied.emit(self.preset_id))
        btn_row.addWidget(apply_btn)
        layout.addLayout(btn_row)


class MainWindow(QMainWindow):
    """Main window for MacBook Audio Control Center."""

    def __init__(self, backend: AudioBackend):
        super().__init__()
        self.backend = backend
        self.setWindowTitle("MacBook Audio Control Center (CachyOS PipeWire)")
        self.resize(1020, 740)
        self.setMinimumSize(880, 620)
        self.setStyleSheet(DARK_STYLESHEET)

        # Central widget and tabs
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Top Header Bar
        main_layout.addWidget(self._create_header_bar())

        # Main Tab Widget
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs, stretch=1)

        # Functional tabs
        self.tab_dashboard = self._create_tab_dashboard()
        self.tab_quantum = self._create_tab_quantum()
        self.tab_profiles = self._create_tab_profiles()
        self.tab_hardware = self._create_tab_hardware()
        self.tab_bluetooth = self._create_tab_bluetooth()
        self.tab_diagnostics = self._create_tab_diagnostics()

        self.tabs.addTab(self.tab_dashboard, "⚡ Dashboard & Presets")
        self.tabs.addTab(self.tab_quantum, "⏱️ Buffer Size, Bitrate & Clock")
        self.tabs.addTab(self.tab_profiles, "🎛️ Audio Profiles")
        self.tabs.addTab(self.tab_hardware, "🔊 Hardware & Devices")
        self.tabs.addTab(self.tab_bluetooth, "📶 Bluetooth Audio Hub")
        self.tabs.addTab(self.tab_diagnostics, "🔍 Diagnostics & Logs")

        # Bottom Status Bar
        self.status_bar_label = QLabel("Ready")
        self.status_bar_label.setStyleSheet("color: #a9b1d6; font-size: 11px;")
        main_layout.addWidget(self.status_bar_label)

        # Auto refresh timer (every 6 seconds)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._background_refresh)
        self.refresh_timer.start(6000)

        # Debounce timer for slider adjustments
        self.slider_timer = QTimer(self)
        self.slider_timer.setSingleShot(True)

        # Cache of existing sink rows to prevent recreate stutter
        self.sink_widgets: Dict[int, Dict[str, Any]] = {}

        # Real-time jack sensing polling timer (every 1 second)
        self.jack_timer = QTimer(self)
        self.jack_timer.timeout.connect(self._check_jack_polling)
        self.jack_timer.start(1000)

        # Apply initial routing and ensure background watchdog daemon is running
        self.backend.apply_audio_routing()
        wd_stat = self.backend.get_watchdog_status()
        if not wd_stat.get("running"):
            self.backend.start_watchdog()

        # Initial full refresh
        self.refresh_all()

    def _create_header_bar(self) -> QWidget:
        header = QFrame()
        header.setStyleSheet("""
            QFrame {
                background-color: #1f2335;
                border: 1px solid #292e42;
                border-radius: 8px;
                padding: 10px 16px;
            }
        """)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(8, 6, 8, 6)

        # Title and system subtitle
        title_box = QVBoxLayout()
        title = QLabel("MacBook Audio Control Center")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #7aa2f7;")
        sub = QLabel("PipeWire 1.6 • WirePlumber 0.5 • Cirrus Logic CS4208 • Broadwell HDMI")
        sub.setStyleSheet("font-size: 11px; color: #787c99;")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        hl.addLayout(title_box)

        hl.addStretch()

        # Status Badges
        self.badge_profile = QLabel("Profile: Loading...")
        self.badge_profile.setStyleSheet("background-color: #24283b; color: #06d6a0; border: 1px solid #06d6a0; border-radius: 4px; padding: 4px 10px; font-weight: bold;")
        hl.addWidget(self.badge_profile)

        self.badge_quantum = QLabel("Quantum: 1024")
        self.badge_quantum.setStyleSheet("background-color: #24283b; color: #ffbe0b; border: 1px solid #ffbe0b; border-radius: 4px; padding: 4px 10px; font-weight: bold;")
        hl.addWidget(self.badge_quantum)

        self.badge_rate = QLabel("Rate: 48 kHz")
        self.badge_rate.setStyleSheet("background-color: #24283b; color: #7aa2f7; border: 1px solid #7aa2f7; border-radius: 4px; padding: 4px 10px; font-weight: bold;")
        hl.addWidget(self.badge_rate)

        self.badge_bitrate = QLabel("Bitrate: 2,304 kbps")
        self.badge_bitrate.setStyleSheet("background-color: #24283b; color: #bd93f9; border: 1px solid #bd93f9; border-radius: 4px; padding: 4px 10px; font-weight: bold;")
        hl.addWidget(self.badge_bitrate)

        # Quick refresh button
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh_all)
        hl.addWidget(refresh_btn)

        return header

    # -------------------------------------------------------------------------
    # TAB 1: DASHBOARD & PRESETS
    # -------------------------------------------------------------------------
    def _create_tab_dashboard(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)

        # Summary Banner
        summary_group = QGroupBox("System Audio Overview")
        sg_layout = QGridLayout(summary_group)
        sg_layout.setContentsMargins(16, 14, 16, 14)
        sg_layout.setHorizontalSpacing(24)

        self.lbl_active_card = QLabel("Audio Card: CS4208 Analog / Pro")
        self.lbl_active_card.setStyleSheet("font-size: 13px; font-weight: bold;")
        sg_layout.addWidget(self.lbl_active_card, 0, 0)

        self.lbl_default_sink = QLabel("Default Output: Built-in Audio Pro")
        self.lbl_default_sink.setStyleSheet("font-size: 13px; font-weight: bold;")
        sg_layout.addWidget(self.lbl_default_sink, 0, 1)

        self.lbl_live_latency = QLabel("Latency: ~21.3 ms (Buffer: 1024 @ 48kHz)")
        self.lbl_live_latency.setStyleSheet("font-size: 13px; font-weight: bold; color: #06d6a0;")
        sg_layout.addWidget(self.lbl_live_latency, 1, 0)

        self.lbl_live_bitrate = QLabel("Bitrate: 2,304 kbps (24-bit Stereo @ 48kHz)")
        self.lbl_live_bitrate.setStyleSheet("font-size: 13px; font-weight: bold; color: #bd93f9;")
        sg_layout.addWidget(self.lbl_live_bitrate, 1, 1)

        self.lbl_fix_status = QLabel("WirePlumber Pro Fix: Active (Period 1024)")
        self.lbl_fix_status.setStyleSheet("font-size: 13px; font-weight: bold; color: #7aa2f7;")
        sg_layout.addWidget(self.lbl_fix_status, 2, 0)

        self.lbl_hdmi_overview = QLabel("HDMI Audio: Disabled (Off)")
        self.lbl_hdmi_overview.setStyleSheet("font-size: 13px; font-weight: bold; color: #a9b1d6;")
        sg_layout.addWidget(self.lbl_hdmi_overview, 2, 1)

        self.lbl_routing_overview = QLabel("Audio Routing: Exclusive (Auto)")
        self.lbl_routing_overview.setStyleSheet("font-size: 13px; font-weight: bold; color: #7aa2f7;")
        sg_layout.addWidget(self.lbl_routing_overview, 3, 0)

        self.lbl_jack_overview = QLabel("3.5mm Headphone Jack: Disconnected")
        self.lbl_jack_overview.setStyleSheet("font-size: 13px; font-weight: bold; color: #a9b1d6;")
        sg_layout.addWidget(self.lbl_jack_overview, 3, 1)

        self.lbl_mic_overview = QLabel("Active Microphone: Internal Digital Mic 🎤")
        self.lbl_mic_overview.setStyleSheet("font-size: 13px; font-weight: bold; color: #06d6a0;")
        sg_layout.addWidget(self.lbl_mic_overview, 4, 0, 1, 2)

        layout.addWidget(summary_group)

        # Presets Section
        presets_group = QGroupBox("One-Click Audio Presets")
        pg_layout = QVBoxLayout(presets_group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        self.presets_grid = QVBoxLayout(scroll_content)
        self.presets_grid.setSpacing(10)

        for p in self.backend.get_presets():
            card = PresetCardWidget(p)
            card.applied.connect(self._apply_preset_callback)
            self.presets_grid.addWidget(card)

        scroll.setWidget(scroll_content)
        pg_layout.addWidget(scroll)

        # Bottom restore button
        bottom_row = QHBoxLayout()
        restore_fix_btn = QPushButton("🛡️ Restore Baseline Pro-Audio Lag Fix")
        restore_fix_btn.setStyleSheet("background-color: #06d6a0; color: #15161e; font-weight: bold; padding: 10px 20px;")
        restore_fix_btn.clicked.connect(lambda: self._apply_preset_callback("pro_audio_fix"))
        bottom_row.addWidget(restore_fix_btn)
        bottom_row.addStretch()

        pg_layout.addLayout(bottom_row)
        layout.addWidget(presets_group, stretch=1)

        return widget

    # -------------------------------------------------------------------------
    # TAB 2: BUFFER SIZE, BITRATE & CLOCK RATES
    # -------------------------------------------------------------------------
    def _create_tab_quantum(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)

        # Quantum (Buffer size) section
        quantum_group = QGroupBox("Buffer Size / Quantum (Lower = Lower Latency, Higher = Stable)")
        q_layout = QVBoxLayout(quantum_group)

        self.btn_group_quantum = QButtonGroup(self)
        q_grid = QGridLayout()
        self.quantum_values = [32, 64, 128, 256, 512, 1024, 2048, 4096]

        col = 0
        row = 0
        for q in self.quantum_values:
            rb = QRadioButton(f"{q} samples")
            rb.setProperty("quantum", q)
            if q == 1024:
                rb.setChecked(True)
            self.btn_group_quantum.addButton(rb)
            q_grid.addWidget(rb, row, col)
            col += 1
            if col >= 4:
                col = 0
                row += 1

        self.btn_group_quantum.buttonClicked.connect(self._update_calculated_metrics)
        q_layout.addLayout(q_grid)
        layout.addWidget(quantum_group)

        # Clock Sample Rate section
        rate_group = QGroupBox("Clock Sample Rate (Master Audio Clock)")
        r_layout = QVBoxLayout(rate_group)

        self.btn_group_rate = QButtonGroup(self)
        r_grid = QGridLayout()
        self.rate_values = [44100, 48000, 88200, 96000, 176400, 192000]

        col = 0
        row = 0
        for r in self.rate_values:
            rb = QRadioButton(f"{r / 1000:.1f} kHz ({r} Hz)")
            rb.setProperty("rate", r)
            if r == 48000:
                rb.setChecked(True)
            self.btn_group_rate.addButton(rb)
            r_grid.addWidget(rb, row, col)
            col += 1
            if col >= 3:
                col = 0
                row += 1

        self.btn_group_rate.buttonClicked.connect(self._update_calculated_metrics)
        r_layout.addLayout(r_grid)
        layout.addWidget(rate_group)

        # Bit Depth & Bitrate Tuning section
        bitrate_group = QGroupBox("Bit Depth & Uncompressed Audio Bitrate Tuning")
        b_layout = QVBoxLayout(bitrate_group)

        self.btn_group_depth = QButtonGroup(self)
        b_grid = QHBoxLayout()

        depths = [
            (16, "16-bit (Standard / CD Quality)"),
            (24, "24-bit (Studio Master - Default)"),
            (32, "32-bit Float (Ultra Dynamic Range)")
        ]
        for depth_val, label_text in depths:
            rb = QRadioButton(label_text)
            rb.setProperty("bit_depth", depth_val)
            if depth_val == 24:
                rb.setChecked(True)
            self.btn_group_depth.addButton(rb)
            b_grid.addWidget(rb)

        self.btn_group_depth.buttonClicked.connect(self._update_calculated_metrics)
        b_layout.addLayout(b_grid)

        # Metric readout banner
        metric_box = QFrame()
        metric_box.setStyleSheet("background-color: #24283b; border: 1px solid #414868; border-radius: 6px; padding: 10px;")
        mb_layout = QHBoxLayout(metric_box)

        self.lbl_calculated_latency = QLabel("Calculated Latency: 21.33 ms")
        self.lbl_calculated_latency.setStyleSheet("color: #06d6a0; font-size: 13px; font-weight: bold;")
        mb_layout.addWidget(self.lbl_calculated_latency)

        mb_layout.addStretch()

        self.lbl_calculated_bitrate = QLabel("Uncompressed Bitrate: 2,304 kbps (2.3 Mbps)")
        self.lbl_calculated_bitrate.setStyleSheet("color: #bd93f9; font-size: 13px; font-weight: bold;")
        mb_layout.addWidget(self.lbl_calculated_bitrate)

        b_layout.addWidget(metric_box)
        layout.addWidget(bitrate_group)

        # Persistence & actions
        opts_group = QGroupBox("Application Mode & Persistence")
        opts_layout = QVBoxLayout(opts_group)

        self.chk_persist = QCheckBox("Save persistently to ~/.config/pipewire (10-quantum.conf)")
        self.chk_persist.setChecked(True)
        opts_layout.addWidget(self.chk_persist)

        self.chk_sync_wp = QCheckBox("Auto-scale WirePlumber period-size & headroom to match quantum")
        self.chk_sync_wp.setChecked(True)
        opts_layout.addWidget(self.chk_sync_wp)

        btns_row = QHBoxLayout()
        apply_live_btn = QPushButton("⚡ Apply Changes Immediately")
        apply_live_btn.setStyleSheet("background-color: #7aa2f7; color: #15161e; font-weight: bold; padding: 10px 20px;")
        apply_live_btn.clicked.connect(self._apply_quantum_and_rate)
        btns_row.addWidget(apply_live_btn)

        reset_auto_btn = QPushButton("🔄 Reset Overrides (Auto Negotiation)")
        reset_auto_btn.clicked.connect(self._reset_quantum_overrides)
        btns_row.addWidget(reset_auto_btn)

        restart_svc_btn = QPushButton("🔁 Restart Audio Services")
        restart_svc_btn.setStyleSheet("background-color: #ffbe0b; color: #15161e; font-weight: bold; padding: 10px 20px;")
        restart_svc_btn.clicked.connect(self._restart_services)
        btns_row.addWidget(restart_svc_btn)

        opts_layout.addLayout(btns_row)
        layout.addWidget(opts_group)

        layout.addStretch()
        return widget

    def _update_calculated_metrics(self):
        selected_q = 1024
        for btn in self.btn_group_quantum.buttons():
            if btn.isChecked():
                selected_q = btn.property("quantum")
                break

        selected_r = 48000
        for btn in self.btn_group_rate.buttons():
            if btn.isChecked():
                selected_r = btn.property("rate")
                break

        selected_depth = 24
        for btn in self.btn_group_depth.buttons():
            if btn.isChecked():
                selected_depth = btn.property("bit_depth")
                break

        latency_ms = AudioBackend.calculate_latency_ms(selected_q, selected_r)
        _, bitrate_str = AudioBackend.calculate_pcm_bitrate(selected_r, selected_depth, 2)

        self.lbl_calculated_latency.setText(f"Calculated Latency: {latency_ms:.2f} ms ({selected_q} samples @ {selected_r/1000:.1f} kHz)")
        self.lbl_calculated_bitrate.setText(f"Uncompressed Bitrate: {bitrate_str} ({selected_depth}-bit Stereo)")

    # -------------------------------------------------------------------------
    # TAB 3: AUDIO PROFILES
    # -------------------------------------------------------------------------
    def _create_tab_profiles(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)

        # Card selector
        card_box = QGroupBox("Select Audio Card / Hardware Device")
        cb_layout = QHBoxLayout(card_box)
        self.combo_cards = QComboBox()
        self.combo_cards.currentIndexChanged.connect(self._on_card_selection_changed)
        cb_layout.addWidget(self.combo_cards, stretch=1)
        layout.addWidget(card_box)

        # Profile list
        prof_box = QGroupBox("Available Audio Profiles")
        pb_layout = QVBoxLayout(prof_box)

        self.profile_list = QListWidget()
        self.profile_list.setStyleSheet("""
            QListWidget {
                background-color: #1f2335;
                border: 1px solid #414868;
                border-radius: 6px;
                padding: 6px;
            }
            QListWidget::item {
                padding: 10px;
                border-bottom: 1px solid #292e42;
                border-radius: 4px;
            }
            QListWidget::item:selected {
                background-color: #7aa2f7;
                color: #15161e;
                font-weight: bold;
            }
        """)
        pb_layout.addWidget(self.profile_list)

        # Apply profile button
        btn_row = QHBoxLayout()
        self.btn_apply_profile = QPushButton("Apply Selected Profile")
        self.btn_apply_profile.setStyleSheet("background-color: #7aa2f7; color: #15161e; font-weight: bold; padding: 10px 24px;")
        self.btn_apply_profile.clicked.connect(self._apply_selected_profile)
        btn_row.addWidget(self.btn_apply_profile)
        btn_row.addStretch()

        note = QLabel(
            "ℹ️ <b>Pro Audio:</b> Direct access to individual DACs, lowest latency. Jitter is resolved via WirePlumber rule.<br>"
            "ℹ️ <b>Analog Stereo Duplex:</b> Standard consumer stereo routing with automatic headphone jack switching.<br>"
            "ℹ️ <b>Off:</b> Disables the card completely to free DSP or power."
        )
        note.setStyleSheet("color: #a9b1d6; font-size: 12px; margin-top: 8px;")
        pb_layout.addWidget(note)
        pb_layout.addLayout(btn_row)

        layout.addWidget(prof_box, stretch=1)
        return widget

    # -------------------------------------------------------------------------
    # TAB 4: HARDWARE & SINK DEVICES
    # -------------------------------------------------------------------------
    def _create_tab_hardware(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)

        # Cirrus Logic CS4208 Hardware Controls
        alsa_group = QGroupBox("MacBook Broadwell-U Hardware ALSA Controls (Cirrus Logic CS4208)")
        ag_layout = QVBoxLayout(alsa_group)

        # Master volume & switch
        master_row = QHBoxLayout()
        master_label = QLabel("🎚️ Master Hardware DAC:")
        master_label.setFixedWidth(180)
        self.btn_master_switch = QPushButton("Unmuted")
        self.btn_master_switch.setCheckable(True)
        self.btn_master_switch.setChecked(True)
        self.btn_master_switch.setStyleSheet("background-color: #06d6a0; color: #15161e; font-weight: bold;")
        self.btn_master_switch.toggled.connect(self._toggle_master_switch)
        master_row.addWidget(master_label)
        master_row.addWidget(self.btn_master_switch)

        self.slider_master = QSlider(Qt.Horizontal)
        self.slider_master.setRange(0, 100)
        self.slider_master.setValue(100)
        self.slider_master.valueChanged.connect(self._change_master_volume)
        master_row.addWidget(self.slider_master)

        self.lbl_master_val = QLabel("100%")
        self.lbl_master_val.setFixedWidth(45)
        master_row.addWidget(self.lbl_master_val)
        ag_layout.addLayout(master_row)

        # Speaker control
        spk_row = QHBoxLayout()
        spk_label = QLabel("🔊 Built-in Speakers:")
        spk_label.setFixedWidth(180)
        self.btn_spk_switch = QPushButton("Unmuted")
        self.btn_spk_switch.setCheckable(True)
        self.btn_spk_switch.setChecked(True)
        self.btn_spk_switch.setStyleSheet("background-color: #06d6a0; color: #15161e; font-weight: bold;")
        self.btn_spk_switch.toggled.connect(self._toggle_speaker_switch)
        spk_row.addWidget(spk_label)
        spk_row.addWidget(self.btn_spk_switch)

        self.slider_spk = QSlider(Qt.Horizontal)
        self.slider_spk.setRange(0, 100)
        self.slider_spk.setValue(100)
        self.slider_spk.valueChanged.connect(self._change_speaker_volume)
        spk_row.addWidget(self.slider_spk)

        self.lbl_spk_val = QLabel("100%")
        self.lbl_spk_val.setFixedWidth(45)
        spk_row.addWidget(self.lbl_spk_val)
        ag_layout.addLayout(spk_row)

        # Headphone control
        hp_row = QHBoxLayout()
        hp_label = QLabel("🎧 Headphone Jack:")
        hp_label.setFixedWidth(180)
        self.btn_hp_switch = QPushButton("Unmuted")
        self.btn_hp_switch.setCheckable(True)
        self.btn_hp_switch.setChecked(True)
        self.btn_hp_switch.setStyleSheet("background-color: #06d6a0; color: #15161e; font-weight: bold;")
        self.btn_hp_switch.toggled.connect(self._toggle_headphone_switch)
        hp_row.addWidget(hp_label)
        hp_row.addWidget(self.btn_hp_switch)

        self.slider_hp = QSlider(Qt.Horizontal)
        self.slider_hp.setRange(0, 100)
        self.slider_hp.setValue(100)
        self.slider_hp.valueChanged.connect(self._change_headphone_volume)
        hp_row.addWidget(self.slider_hp)

        self.lbl_hp_val = QLabel("100%")
        self.lbl_hp_val.setFixedWidth(45)
        hp_row.addWidget(self.lbl_hp_val)
        ag_layout.addLayout(hp_row)

        # Auto-Mute Mode control
        automute_row = QHBoxLayout()
        am_label = QLabel("⚙️ Auto-Mute Mode:")
        am_label.setFixedWidth(180)
        self.btn_automute = QPushButton("Disabled (Simultaneous Speakers & HP)")
        self.btn_automute.setCheckable(True)
        self.btn_automute.setChecked(False)
        self.btn_automute.setStyleSheet("background-color: #24283b; color: #7aa2f7; font-weight: bold;")
        self.btn_automute.toggled.connect(self._toggle_automute_switch)
        automute_row.addWidget(am_label)
        automute_row.addWidget(self.btn_automute)
        automute_row.addStretch()
        ag_layout.addLayout(automute_row)

        # Smart Jack Auto-Routing & Simultaneous Output section
        routing_frame = QFrame()
        routing_frame.setStyleSheet("""
            QFrame {
                background-color: #1a1b26;
                border: 1px solid #414868;
                border-radius: 6px;
                padding: 10px;
            }
        """)
        rf_layout = QVBoxLayout(routing_frame)
        rf_layout.setContentsMargins(10, 8, 10, 8)
        rf_layout.setSpacing(6)

        rf_top = QHBoxLayout()
        rf_top.addWidget(QLabel("<b>🔀 Smart Jack Routing & Multi-Output:</b>"))
        self.lbl_jack_state_badge = QLabel("🎧 3.5mm Jack: Disconnected")
        self.lbl_jack_state_badge.setStyleSheet("background-color: #24283b; color: #a9b1d6; border-radius: 4px; padding: 3px 8px; font-weight: bold; font-size: 11px;")
        rf_top.addWidget(self.lbl_jack_state_badge)
        rf_top.addStretch()
        rf_layout.addLayout(rf_top)

        self.chk_simultaneous_output = QCheckBox("🔊+🎧 Simultaneous Multi-Output (Play Speakers & Headphones Together)")
        self.chk_simultaneous_output.setStyleSheet("font-size: 13px; font-weight: bold; color: #7aa2f7;")
        self.chk_simultaneous_output.setChecked(False)
        self.chk_simultaneous_output.toggled.connect(self._on_simultaneous_output_toggled)
        rf_layout.addWidget(self.chk_simultaneous_output)

        lbl_sim_desc = QLabel(
            "Default: OFF (Exclusive Auto-Routing). When OFF, inserting headphones immediately and exclusively "
            "routes audio to headphones and mutes laptop speakers. When unplugged, audio returns to speakers. "
            "Enable this checkbox ONLY if you want audio to play out of both speakers and headphones simultaneously."
        )
        lbl_sim_desc.setWordWrap(True)
        lbl_sim_desc.setStyleSheet("color: #787c99; font-size: 11px;")
        rf_layout.addWidget(lbl_sim_desc)

        ag_layout.addWidget(routing_frame)

        # Microphone & 3.5mm Headset Input (CS4208 Pin 0x18 & 0x1c) section
        mic_frame = QFrame()
        mic_frame.setStyleSheet("""
            QFrame {
                background-color: #1a1b26;
                border: 1px solid #414868;
                border-radius: 6px;
                padding: 10px;
            }
        """)
        mf_layout = QVBoxLayout(mic_frame)
        mf_layout.setContentsMargins(10, 8, 10, 8)
        mf_layout.setSpacing(6)

        mf_top = QHBoxLayout()
        mf_top.addWidget(QLabel("<b>🎤 Microphone & 3.5mm Headset Input (CS4208 Pin 0x18 / 0x1c):</b>"))
        self.lbl_mic_pin_badge = QLabel("Pin 0x18 Patch: Active")
        self.lbl_mic_pin_badge.setStyleSheet("background-color: #06d6a0; color: #15161e; border-radius: 4px; padding: 3px 8px; font-weight: bold; font-size: 11px;")
        mf_top.addWidget(self.lbl_mic_pin_badge)
        mf_top.addStretch()
        mf_layout.addLayout(mf_top)

        # Input Source selector row
        src_row = QHBoxLayout()
        src_lbl = QLabel("🎙️ Active Input Source:")
        src_lbl.setFixedWidth(170)
        src_row.addWidget(src_lbl)

        self.combo_mic_source = QComboBox()
        self.combo_mic_source.addItem("🎧 3.5mm Headset Microphone (External Jack - Pin 0x18)", "Mic")
        self.combo_mic_source.addItem("💻 Built-in Digital Microphone (Internal - Pin 0x1c)", "Internal Mic")
        self.combo_mic_source.currentIndexChanged.connect(self._on_mic_source_changed)
        src_row.addWidget(self.combo_mic_source, stretch=1)
        mf_layout.addLayout(src_row)

        # Smart Mic Auto-Switch checkbox
        self.chk_smart_mic_switch = QCheckBox("🔀 Smart Mic Auto-Switch (Auto-select 3.5mm Headset Mic when headphones plugged in)")
        self.chk_smart_mic_switch.setStyleSheet("font-size: 12px; font-weight: bold; color: #06d6a0;")
        self.chk_smart_mic_switch.setChecked(True)
        self.chk_smart_mic_switch.toggled.connect(self._on_smart_mic_switch_toggled)
        mf_layout.addWidget(self.chk_smart_mic_switch)

        # Capture Volume & Switch row
        cap_row = QHBoxLayout()
        cap_lbl = QLabel("🎚️ ADC Capture Gain:")
        cap_lbl.setFixedWidth(170)
        self.btn_capture_switch = QPushButton("Capturing")
        self.btn_capture_switch.setCheckable(True)
        self.btn_capture_switch.setChecked(True)
        self.btn_capture_switch.setStyleSheet("background-color: #06d6a0; color: #15161e; font-weight: bold;")
        self.btn_capture_switch.toggled.connect(self._toggle_capture_switch)
        cap_row.addWidget(cap_lbl)
        cap_row.addWidget(self.btn_capture_switch)

        self.slider_capture = QSlider(Qt.Horizontal)
        self.slider_capture.setRange(0, 100)
        self.slider_capture.setValue(100)
        self.slider_capture.valueChanged.connect(self._change_capture_volume)
        cap_row.addWidget(self.slider_capture)

        self.lbl_capture_val = QLabel("100%")
        self.lbl_capture_val.setFixedWidth(45)
        cap_row.addWidget(self.lbl_capture_val)
        mf_layout.addLayout(cap_row)

        # Boost selectors row
        boost_row = QHBoxLayout()
        boost_row.addWidget(QLabel("🎧 Headset Mic Boost:"))
        self.combo_headset_boost = QComboBox()
        self.combo_headset_boost.addItem("0 (-20 dB)", 0)
        self.combo_headset_boost.addItem("1 (-10 dB)", 1)
        self.combo_headset_boost.addItem("2 (0 dB)", 2)
        self.combo_headset_boost.addItem("3 (+10 dB Recommended)", 3)
        self.combo_headset_boost.setCurrentIndex(3)
        self.combo_headset_boost.currentIndexChanged.connect(self._on_headset_boost_changed)
        boost_row.addWidget(self.combo_headset_boost)

        boost_row.addSpacing(16)
        boost_row.addWidget(QLabel("💻 Internal Mic Boost:"))
        self.combo_internal_boost = QComboBox()
        self.combo_internal_boost.addItem("0 (-20 dB)", 0)
        self.combo_internal_boost.addItem("1 (-10 dB)", 1)
        self.combo_internal_boost.addItem("2 (0 dB Recommended)", 2)
        self.combo_internal_boost.setCurrentIndex(2)
        self.combo_internal_boost.currentIndexChanged.connect(self._on_internal_boost_changed)
        boost_row.addWidget(self.combo_internal_boost)
        boost_row.addStretch()
        mf_layout.addLayout(boost_row)

        ag_layout.addWidget(mic_frame)

        # Quick hardware buttons
        hw_btns = QHBoxLayout()
        auto_route_btn = QPushButton("🎯 Auto Route Now (Check Jack)")
        auto_route_btn.clicked.connect(self._apply_auto_routing_now)
        hw_btns.addWidget(auto_route_btn)

        unmute_both_btn = QPushButton("🔊+🎧 Force Unmute Both")
        unmute_both_btn.clicked.connect(self._unmute_both_alsa)
        hw_btns.addWidget(unmute_both_btn)

        speaker_only_btn = QPushButton("🔊 Speaker Only")
        speaker_only_btn.clicked.connect(self._speaker_only)
        hw_btns.addWidget(speaker_only_btn)

        hp_only_btn = QPushButton("🎧 Headphone Only")
        hp_only_btn.clicked.connect(self._headphone_only)
        hw_btns.addWidget(hp_only_btn)

        mute_all_btn = QPushButton("🔇 Mute All Analog")
        mute_all_btn.clicked.connect(self._mute_all_alsa)
        hw_btns.addWidget(mute_all_btn)

        ag_layout.addLayout(hw_btns)
        layout.addWidget(alsa_group)

        # HDMI Quick Controller
        hdmi_group = QGroupBox("Broadwell HDMI Audio (Intel Broadwell-U)")
        hg_layout = QHBoxLayout(hdmi_group)

        self.lbl_hdmi_status = QLabel("HDMI Status: Inactive (Off)")
        self.lbl_hdmi_status.setStyleSheet("font-weight: bold; color: #a9b1d6;")
        hg_layout.addWidget(self.lbl_hdmi_status)
        hg_layout.addStretch()

        self.btn_toggle_hdmi = QPushButton("Enable HDMI Output (Stereo)")
        self.btn_toggle_hdmi.setStyleSheet("background-color: #7aa2f7; color: #15161e; font-weight: bold;")
        self.btn_toggle_hdmi.clicked.connect(self._toggle_hdmi)
        hg_layout.addWidget(self.btn_toggle_hdmi)

        layout.addWidget(hdmi_group)

        # PipeWire Sinks list
        sinks_group = QGroupBox("Active Audio Output Devices (PipeWire Sinks)")
        sg_layout = QVBoxLayout(sinks_group)
        self.sinks_scroll = QScrollArea()
        self.sinks_scroll.setWidgetResizable(True)
        self.sinks_container = QWidget()
        self.sinks_layout = QVBoxLayout(self.sinks_container)
        self.sinks_scroll.setWidget(self.sinks_container)
        sg_layout.addWidget(self.sinks_scroll)
        layout.addWidget(sinks_group, stretch=1)

        return widget

    # -------------------------------------------------------------------------
    # TAB 5: BLUETOOTH AUDIO HUB
    # -------------------------------------------------------------------------
    def _create_tab_bluetooth(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)

        bt_header = QHBoxLayout()
        bt_title = QLabel("Paired & Connected Bluetooth Devices")
        bt_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #7aa2f7;")
        bt_header.addWidget(bt_title)
        bt_header.addStretch()

        scan_btn = QPushButton("🔄 Refresh Bluetooth Devices")
        scan_btn.clicked.connect(self._refresh_bluetooth)
        bt_header.addWidget(scan_btn)
        layout.addLayout(bt_header)

        # Bluetooth list
        self.bt_list = QListWidget()
        self.bt_list.setStyleSheet("""
            QListWidget {
                background-color: #1f2335;
                border: 1px solid #414868;
                border-radius: 6px;
                padding: 6px;
            }
            QListWidget::item {
                padding: 12px;
                border-bottom: 1px solid #292e42;
                border-radius: 6px;
                margin-bottom: 4px;
            }
            QListWidget::item:selected {
                background-color: #292e42;
                border: 1px solid #7aa2f7;
            }
        """)
        self.bt_list.itemSelectionChanged.connect(self._on_bt_device_selected)
        layout.addWidget(self.bt_list, stretch=1)

        # Actions for selected Bluetooth device
        bt_actions = QGroupBox("Device Controls & Audio Profiles")
        ba_layout = QVBoxLayout(bt_actions)

        row1 = QHBoxLayout()
        self.btn_bt_connect = QPushButton("Connect Device")
        self.btn_bt_connect.setStyleSheet("background-color: #06d6a0; color: #15161e; font-weight: bold; padding: 8px 18px;")
        self.btn_bt_connect.clicked.connect(self._connect_selected_bt)
        row1.addWidget(self.btn_bt_connect)

        self.btn_bt_disconnect = QPushButton("Disconnect Device")
        self.btn_bt_disconnect.setStyleSheet("background-color: #f7768e; color: #15161e; font-weight: bold; padding: 8px 18px;")
        self.btn_bt_disconnect.clicked.connect(self._disconnect_selected_bt)
        row1.addWidget(self.btn_bt_disconnect)

        self.btn_bt_set_default = QPushButton("Set As Default Audio Sink")
        self.btn_bt_set_default.clicked.connect(self._set_selected_bt_as_default)
        row1.addWidget(self.btn_bt_set_default)
        ba_layout.addLayout(row1)

        # Profile / Codec selection
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Bluetooth Profile / Codec:"))
        self.combo_bt_profile = QComboBox()
        self.combo_bt_profile.addItems([
            "A2DP Sink - High Fidelity (AAC / SBC-XQ / LDAC)",
            "Headset Head Unit - HFP/HSP (Voice / Mic Calls)",
            "Off"
        ])
        row2.addWidget(self.combo_bt_profile, stretch=1)

        btn_apply_bt_prof = QPushButton("Switch Profile")
        btn_apply_bt_prof.clicked.connect(self._apply_bt_profile)
        row2.addWidget(btn_apply_bt_prof)
        ba_layout.addLayout(row2)

        # LDAC / SBC-XQ Quality Tuning
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("A2DP Bitrate Quality:"))
        self.combo_bt_quality = QComboBox()
        self.combo_bt_quality.addItem("High Quality (LDAC 990 kbps / SBC-XQ 453 kbps)", "hq")
        self.combo_bt_quality.addItem("Standard Quality (LDAC 660 kbps)", "sq")
        self.combo_bt_quality.addItem("Mobile / Eco Quality (LDAC 330 kbps)", "mq")
        self.combo_bt_quality.addItem("Auto Adaptive Bitrate", "auto")
        row3.addWidget(self.combo_bt_quality, stretch=1)

        btn_apply_bt_qual = QPushButton("Save Bitrate Rule")
        btn_apply_bt_qual.clicked.connect(self._apply_bt_quality)
        row3.addWidget(btn_apply_bt_qual)
        ba_layout.addLayout(row3)

        layout.addWidget(bt_actions)
        return widget

    # -------------------------------------------------------------------------
    # TAB 6: DIAGNOSTICS & LOGS
    # -------------------------------------------------------------------------
    def _create_tab_diagnostics(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)

        diag_header = QHBoxLayout()
        diag_title = QLabel("System PipeWire & WirePlumber Diagnostics")
        diag_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #7aa2f7;")
        diag_header.addWidget(diag_title)
        diag_header.addStretch()

        refresh_diag_btn = QPushButton("🔄 Refresh Diagnostics")
        refresh_diag_btn.clicked.connect(self._refresh_diagnostics)
        diag_header.addWidget(refresh_diag_btn)
        layout.addLayout(diag_header)

        self.txt_diagnostics = QTextEdit()
        self.txt_diagnostics.setReadOnly(True)
        layout.addWidget(self.txt_diagnostics, stretch=1)

        return widget

    # -------------------------------------------------------------------------
    # CONTROLLER LOGIC & REFRESH METHODS
    # -------------------------------------------------------------------------
    def refresh_all(self):
        """Update all tabs and data models."""
        self._refresh_header_and_overview()
        self._refresh_quantum_tab()
        self._refresh_cards_and_profiles()
        self._refresh_hardware_and_sinks()
        self._refresh_bluetooth()
        self._refresh_diagnostics()
        self.status_bar_label.setText("Refreshed audio state at " + QtCore.QTime.currentTime().toString())

    def _background_refresh(self):
        """Lighter refresh triggered periodically."""
        self._refresh_header_and_overview()
        self._check_jack_polling()

    def _refresh_header_and_overview(self):
        settings = self.backend.get_pipewire_metadata_settings()
        wp_status = self.backend.get_wpctl_status()
        hdmi_stat = self.backend.get_hdmi_status()

        q = settings.get("clock.force-quantum") or settings.get("clock.quantum") or 1024
        r = settings.get("clock.force-rate") or settings.get("clock.rate") or 48000

        self.badge_quantum.setText(f"Quantum: {q}")
        self.badge_rate.setText(f"Rate: {r // 1000} kHz")

        pw_conf = self.backend.get_saved_pipewire_config()
        bit_depth = pw_conf.get("bit_depth", 24)
        _, bitrate_str = AudioBackend.calculate_pcm_bitrate(r, bit_depth)
        self.badge_bitrate.setText(f"Bitrate: {bitrate_str.split('(')[0].strip()}")

        # Active internal profile
        cards = self.backend.get_cards()
        active_prof = "Pro Audio"
        for c in cards:
            if "00_1b.0" in c.get("name", ""):
                active_prof = c.get("active_profile", "pro-audio")
                break

        self.badge_profile.setText(f"Profile: {active_prof}")
        self.lbl_active_card.setText(f"Active Profile: {active_prof}")

        default_sink = wp_status.get("default_sink")
        if default_sink:
            self.lbl_default_sink.setText(f"Default Output: {default_sink['name']}")
        else:
            self.lbl_default_sink.setText("Default Output: Unknown")

        latency = AudioBackend.calculate_latency_ms(q, r)
        self.lbl_live_latency.setText(f"Live Latency: ~{latency:.2f} ms ({q} samples @ {r} Hz)")
        self.lbl_live_bitrate.setText(f"Uncompressed Bitrate: {bitrate_str} ({bit_depth}-bit Stereo)")

        pro_conf = self.backend.get_saved_wireplumber_pro_fix()
        if pro_conf.get("enabled"):
            self.lbl_fix_status.setText(f"WirePlumber Rule: Active (Period={pro_conf.get('period_size')})")
            self.lbl_fix_status.setStyleSheet("color: #06d6a0; font-weight: bold;")
        else:
            self.lbl_fix_status.setText("WirePlumber Rule: Inactive")
            self.lbl_fix_status.setStyleSheet("color: #ffbe0b; font-weight: bold;")

        # HDMI status
        if hdmi_stat.get("enabled"):
            self.lbl_hdmi_overview.setText(f"HDMI Audio: Active ({hdmi_stat.get('active_profile')})")
            self.lbl_hdmi_overview.setStyleSheet("color: #06d6a0; font-weight: bold;")
            self.lbl_hdmi_status.setText(f"HDMI Status: Active ({hdmi_stat.get('active_profile')})")
            self.btn_toggle_hdmi.setText("Disable HDMI Output")
        else:
            self.lbl_hdmi_overview.setText("HDMI Audio: Inactive (Off)")
            self.lbl_hdmi_overview.setStyleSheet("color: #a9b1d6;")
            self.lbl_hdmi_status.setText("HDMI Status: Inactive (Off)")
            self.btn_toggle_hdmi.setText("Enable HDMI Output (Stereo)")

        # Jack status and routing overview
        alsa_stat = self.backend.get_alsa_hardware_controls("1")
        jack_plugged = alsa_stat.get("headphone_jack_plugged", False)
        simultaneous = alsa_stat.get("simultaneous_output", False)

        if hasattr(self, "lbl_jack_overview"):
            self.lbl_jack_overview.setText("3.5mm Headphone Jack: " + ("Plugged In 🎧" if jack_plugged else "Disconnected"))
            self.lbl_jack_overview.setStyleSheet("font-size: 13px; font-weight: bold; color: #06d6a0;" if jack_plugged else "font-size: 13px; font-weight: bold; color: #a9b1d6;")

        if hasattr(self, "lbl_routing_overview"):
            if simultaneous:
                self.lbl_routing_overview.setText("Audio Routing: Simultaneous (Speakers + HP)")
                self.lbl_routing_overview.setStyleSheet("font-size: 13px; font-weight: bold; color: #bd93f9;")
            else:
                self.lbl_routing_overview.setText("Audio Routing: Exclusive (" + ("Headphones 🎧" if jack_plugged else "Internal Speakers 🔊") + ")")
                self.lbl_routing_overview.setStyleSheet("font-size: 13px; font-weight: bold; color: #7aa2f7;")

        if hasattr(self, "lbl_mic_overview"):
            cap_src = alsa_stat.get("capture_source", "Internal Mic")
            smart_mic = alsa_stat.get("smart_mic_auto_switch", True)
            mode_tag = " [Auto-Switch ON]" if smart_mic else " [Manual]"
            if cap_src == "Mic":
                self.lbl_mic_overview.setText(f"Active Microphone: 3.5mm Headset Mic (Pin 0x18) 🎙️{mode_tag}")
                self.lbl_mic_overview.setStyleSheet("font-size: 13px; font-weight: bold; color: #06d6a0;")
            else:
                self.lbl_mic_overview.setText(f"Active Microphone: Built-in Digital Mic (Pin 0x1c) 🎤{mode_tag}")
                self.lbl_mic_overview.setStyleSheet("font-size: 13px; font-weight: bold; color: #7aa2f7;")

    def _refresh_quantum_tab(self):
        settings = self.backend.get_pipewire_metadata_settings()
        pw_cfg = self.backend.get_saved_pipewire_config()

        cur_q = settings.get("clock.force-quantum") or settings.get("clock.quantum") or 1024
        cur_r = settings.get("clock.force-rate") or settings.get("clock.rate") or 48000
        cur_depth = pw_cfg.get("bit_depth", 24)

        for btn in self.btn_group_quantum.buttons():
            if btn.property("quantum") == cur_q:
                btn.setChecked(True)
                break

        for btn in self.btn_group_rate.buttons():
            if btn.property("rate") == cur_r:
                btn.setChecked(True)
                break

        for btn in self.btn_group_depth.buttons():
            if btn.property("bit_depth") == cur_depth:
                btn.setChecked(True)
                break

        self._update_calculated_metrics()

    def _apply_quantum_and_rate(self):
        selected_q = 1024
        for btn in self.btn_group_quantum.buttons():
            if btn.isChecked():
                selected_q = btn.property("quantum")
                break

        selected_r = 48000
        for btn in self.btn_group_rate.buttons():
            if btn.isChecked():
                selected_r = btn.property("rate")
                break

        selected_depth = 24
        for btn in self.btn_group_depth.buttons():
            if btn.isChecked():
                selected_depth = btn.property("bit_depth")
                break

        ok_q, msg_q = self.backend.set_live_quantum(selected_q)
        ok_r, msg_r = self.backend.set_live_sample_rate(selected_r)
        msgs = [msg_q, msg_r]

        if self.chk_persist.isChecked():
            ok_pw, msg_pw = self.backend.save_pipewire_quantum_config(
                quantum=selected_q,
                rate=selected_r,
                bit_depth=selected_depth
            )
            msgs.append(msg_pw)

            if self.chk_sync_wp.isChecked():
                ok_wp, msg_wp = self.backend.save_wireplumber_pro_fix(
                    period_size=selected_q,
                    headroom=selected_q
                )
                msgs.append(msg_wp)

        QMessageBox.information(self, "Buffer & Clock Rate Updated", "\n".join(msgs))
        self.refresh_all()

    def _reset_quantum_overrides(self):
        ok_q, msg_q = self.backend.set_live_quantum(0)
        ok_r, msg_r = self.backend.set_live_sample_rate(0)
        QMessageBox.information(self, "Overrides Reset", f"{msg_q}\n{msg_r}")
        self.refresh_all()

    def _restart_services(self):
        reply = QMessageBox.question(
            self,
            "Restart Audio Services",
            "Are you sure you want to restart PipeWire and WirePlumber? Audio playback will briefly pause.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            ok, msg = self.backend.restart_pipewire_services()
            if ok:
                QMessageBox.information(self, "Services Restarted", msg)
            else:
                QMessageBox.critical(self, "Restart Failed", msg)
            self.refresh_all()

    def _refresh_cards_and_profiles(self):
        cards = self.backend.get_cards()
        current_idx = self.combo_cards.currentIndex()
        self.combo_cards.blockSignals(True)
        self.combo_cards.clear()

        for c in cards:
            desc = c.get("description") or c.get("name")
            self.combo_cards.addItem(f"{desc} [{c['name']}]", c)

        self.combo_cards.blockSignals(False)
        if cards:
            if 0 <= current_idx < len(cards):
                self.combo_cards.setCurrentIndex(current_idx)
            else:
                self.combo_cards.setCurrentIndex(0)
            self._on_card_selection_changed(self.combo_cards.currentIndex())

    def _on_card_selection_changed(self, index: int):
        card_data = self.combo_cards.itemData(index)
        if not card_data:
            return

        self.profile_list.clear()
        profiles = card_data.get("profiles", [])
        active_prof = card_data.get("active_profile", "")

        for p in profiles:
            p_name = p["name"]
            p_desc = p.get("description", p_name)
            is_active = (p_name == active_prof)
            prefix = "⭐ [ACTIVE] " if is_active else "   "
            item_text = f"{prefix}{p_desc} ({p_name})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, p_name)
            if is_active:
                item.setForeground(QtGui.QColor("#06d6a0"))
            self.profile_list.addItem(item)
            if is_active:
                self.profile_list.setCurrentItem(item)

    def _apply_selected_profile(self):
        cur_item = self.profile_list.currentItem()
        if not cur_item:
            QMessageBox.warning(self, "No Profile", "Please select a profile from the list.")
            return

        profile_name = cur_item.data(Qt.UserRole)
        card_data = self.combo_cards.currentData()
        if not card_data:
            return

        card_ident = card_data.get("name") or card_data.get("index")
        ok, msg = self.backend.set_card_profile(card_ident, profile_name)
        if ok:
            QMessageBox.information(self, "Profile Changed", msg)
            self.refresh_all()
        else:
            QMessageBox.critical(self, "Error Switching Profile", msg)

    def _refresh_hardware_and_sinks(self):
        # ALSA CS4208 controls
        alsa = self.backend.get_alsa_hardware_controls("1")
        if alsa.get("available"):
            # Master
            self.btn_master_switch.blockSignals(True)
            self.btn_master_switch.setChecked(alsa["master_switch"])
            self.btn_master_switch.setText("Unmuted" if alsa["master_switch"] else "Muted")
            self.btn_master_switch.setStyleSheet(
                "background-color: #06d6a0; color: #15161e; font-weight: bold;"
                if alsa["master_switch"] else
                "background-color: #f7768e; color: #15161e; font-weight: bold;"
            )
            self.btn_master_switch.blockSignals(False)

            self.slider_master.blockSignals(True)
            self.slider_master.setValue(alsa["master_volume"])
            self.lbl_master_val.setText(f"{alsa['master_volume']}%")
            self.slider_master.blockSignals(False)

            # Speaker
            self.btn_spk_switch.blockSignals(True)
            self.btn_spk_switch.setChecked(alsa["speaker_switch"])
            self.btn_spk_switch.setText("Unmuted" if alsa["speaker_switch"] else "Muted")
            self.btn_spk_switch.setStyleSheet(
                "background-color: #06d6a0; color: #15161e; font-weight: bold;"
                if alsa["speaker_switch"] else
                "background-color: #f7768e; color: #15161e; font-weight: bold;"
            )
            self.btn_spk_switch.blockSignals(False)

            self.slider_spk.blockSignals(True)
            self.slider_spk.setValue(alsa["speaker_volume"])
            self.lbl_spk_val.setText(f"{alsa['speaker_volume']}%")
            self.slider_spk.blockSignals(False)

            # Headphone
            self.btn_hp_switch.blockSignals(True)
            self.btn_hp_switch.setChecked(alsa["headphone_switch"])
            self.btn_hp_switch.setText("Unmuted" if alsa["headphone_switch"] else "Muted")
            self.btn_hp_switch.setStyleSheet(
                "background-color: #06d6a0; color: #15161e; font-weight: bold;"
                if alsa["headphone_switch"] else
                "background-color: #f7768e; color: #15161e; font-weight: bold;"
            )
            self.btn_hp_switch.blockSignals(False)

            self.slider_hp.blockSignals(True)
            self.slider_hp.setValue(alsa["headphone_volume"])
            self.lbl_hp_val.setText(f"{alsa['headphone_volume']}%")
            self.slider_hp.blockSignals(False)

            # Auto-Mute Mode
            is_enabled = (alsa["auto_mute_mode"].lower() == "enabled")
            self.btn_automute.blockSignals(True)
            self.btn_automute.setChecked(is_enabled)
            self.btn_automute.setText(
                "Enabled (Headphone Jack Mutes Speakers)" if is_enabled
                else "Disabled (Simultaneous Speakers & HP Allowed)"
            )
            self.btn_automute.blockSignals(False)

            # Jack status badge & simultaneous checkbox
            jack_plugged = alsa.get("headphone_jack_plugged", False)
            simultaneous = alsa.get("simultaneous_output", False)

            if hasattr(self, "lbl_jack_state_badge"):
                if jack_plugged:
                    self.lbl_jack_state_badge.setText("🎧 3.5mm Jack: Plugged In")
                    self.lbl_jack_state_badge.setStyleSheet("background-color: #06d6a0; color: #15161e; border-radius: 4px; padding: 3px 8px; font-weight: bold; font-size: 11px;")
                else:
                    self.lbl_jack_state_badge.setText("🎧 3.5mm Jack: Disconnected")
                    self.lbl_jack_state_badge.setStyleSheet("background-color: #24283b; color: #a9b1d6; border-radius: 4px; padding: 3px 8px; font-weight: bold; font-size: 11px;")

            if hasattr(self, "chk_simultaneous_output"):
                self.chk_simultaneous_output.blockSignals(True)
                self.chk_simultaneous_output.setChecked(simultaneous)
                self.chk_simultaneous_output.blockSignals(False)

            # Microphone & 3.5mm Headset Input controls
            headset_avail = alsa.get("headset_mic_available", False)
            cap_src = alsa.get("capture_source", "Internal Mic")
            smart_mic = alsa.get("smart_mic_auto_switch", True)
            cap_sw = alsa.get("capture_switch", True)
            cap_vol = alsa.get("capture_volume", 100)
            mic_boost = alsa.get("mic_boost", 3)
            int_boost = alsa.get("internal_mic_boost", 2)

            if hasattr(self, "lbl_mic_pin_badge"):
                if headset_avail:
                    self.lbl_mic_pin_badge.setText("Pin 0x18 Patch: Active (Mic + Internal Mic)")
                    self.lbl_mic_pin_badge.setStyleSheet("background-color: #06d6a0; color: #15161e; border-radius: 4px; padding: 3px 8px; font-weight: bold; font-size: 11px;")
                else:
                    self.lbl_mic_pin_badge.setText("Pin 0x18 Patch: Inactive")
                    self.lbl_mic_pin_badge.setStyleSheet("background-color: #ffbe0b; color: #15161e; border-radius: 4px; padding: 3px 8px; font-weight: bold; font-size: 11px;")

            if hasattr(self, "combo_mic_source"):
                self.combo_mic_source.blockSignals(True)
                self.combo_mic_source.setCurrentIndex(0 if cap_src == "Mic" else 1)
                self.combo_mic_source.blockSignals(False)

            if hasattr(self, "chk_smart_mic_switch"):
                self.chk_smart_mic_switch.blockSignals(True)
                self.chk_smart_mic_switch.setChecked(smart_mic)
                self.chk_smart_mic_switch.blockSignals(False)

            if hasattr(self, "btn_capture_switch"):
                self.btn_capture_switch.blockSignals(True)
                self.btn_capture_switch.setChecked(cap_sw)
                self.btn_capture_switch.setText("Capturing" if cap_sw else "Muted")
                self.btn_capture_switch.setStyleSheet(
                    "background-color: #06d6a0; color: #15161e; font-weight: bold;"
                    if cap_sw else
                    "background-color: #f7768e; color: #15161e; font-weight: bold;"
                )
                self.btn_capture_switch.blockSignals(False)

            if hasattr(self, "slider_capture"):
                self.slider_capture.blockSignals(True)
                self.slider_capture.setValue(cap_vol)
                self.lbl_capture_val.setText(f"{cap_vol}%")
                self.slider_capture.blockSignals(False)

            if hasattr(self, "combo_headset_boost"):
                self.combo_headset_boost.blockSignals(True)
                self.combo_headset_boost.setCurrentIndex(max(0, min(3, int(mic_boost))))
                self.combo_headset_boost.blockSignals(False)

            if hasattr(self, "combo_internal_boost"):
                self.combo_internal_boost.blockSignals(True)
                self.combo_internal_boost.setCurrentIndex(max(0, min(2, int(int_boost))))
                self.combo_internal_boost.blockSignals(False)

        # HDMI status
        hdmi_stat = self.backend.get_hdmi_status()
        if hdmi_stat.get("enabled"):
            self.lbl_hdmi_status.setText(f"HDMI Status: Active ({hdmi_stat.get('active_profile')})")
            self.btn_toggle_hdmi.setText("Disable HDMI Output")
        else:
            self.lbl_hdmi_status.setText("HDMI Status: Inactive (Off)")
            self.btn_toggle_hdmi.setText("Enable HDMI Output (Stereo)")

        # PipeWire Sinks (Output Devices) - Update in-place to avoid slider destroy stutter
        status = self.backend.get_wpctl_status()
        sinks = status.get("sinks", [])
        current_sink_ids = set()

        for s in sinks:
            sid = s["id"]
            current_sink_ids.add(sid)
            if sid not in self.sink_widgets:
                row = QFrame()
                row.setStyleSheet("""
                    QFrame {
                        background-color: #24283b;
                        border: 1px solid #414868;
                        border-radius: 6px;
                        padding: 8px;
                    }
                """)
                rl = QHBoxLayout(row)
                rl.setContentsMargins(8, 4, 8, 4)

                name_lbl = QLabel(f"<b>{s['id']}. {s['name']}</b>")
                rl.addWidget(name_lbl)

                def_btn = QPushButton("Set Default")
                def_btn.clicked.connect(lambda _, target_id=sid: self._set_default_sink_callback(target_id))
                rl.addWidget(def_btn)

                def_badge = QLabel("⭐ DEFAULT")
                def_badge.setStyleSheet("background-color: #06d6a0; color: #15161e; font-weight: bold; border-radius: 4px; padding: 2px 6px; font-size: 11px;")
                rl.addWidget(def_badge)

                rl.addStretch()

                vol_slider = QSlider(Qt.Horizontal)
                vol_slider.setRange(0, 150)
                vol_slider.setValue(int(s["volume"] * 100))
                vol_slider.setFixedWidth(160)
                vol_lbl = QLabel(f"{int(s['volume'] * 100)}%")
                vol_lbl.setFixedWidth(45)

                vol_slider.valueChanged.connect(lambda val, target_id=sid, vlbl=vol_lbl: self._on_sink_slider_moved(target_id, val, vlbl))
                rl.addWidget(vol_slider)
                rl.addWidget(vol_lbl)

                mute_btn = QPushButton("Muted" if s.get("muted") else "Mute")
                mute_btn.setCheckable(True)
                mute_btn.setChecked(s.get("muted", False))
                mute_btn.clicked.connect(lambda _, target_id=sid, mbtn=mute_btn: self._toggle_sink_mute(target_id, mbtn))
                rl.addWidget(mute_btn)

                self.sinks_layout.addWidget(row)
                self.sink_widgets[sid] = {
                    "widget": row,
                    "name_lbl": name_lbl,
                    "def_btn": def_btn,
                    "def_badge": def_badge,
                    "vol_slider": vol_slider,
                    "vol_lbl": vol_lbl,
                    "mute_btn": mute_btn,
                }

            # Update state of existing row
            entry = self.sink_widgets[sid]
            entry["name_lbl"].setText(f"<b>{s['id']}. {s['name']}</b>")
            entry["def_badge"].setVisible(bool(s.get("is_default")))
            entry["def_btn"].setVisible(not bool(s.get("is_default")))

            if not entry["vol_slider"].isSliderDown():
                entry["vol_slider"].blockSignals(True)
                entry["vol_slider"].setValue(int(s["volume"] * 100))
                entry["vol_lbl"].setText(f"{int(s['volume'] * 100)}%")
                entry["vol_slider"].blockSignals(False)

            entry["mute_btn"].blockSignals(True)
            entry["mute_btn"].setChecked(s.get("muted", False))
            entry["mute_btn"].setText("Muted" if s.get("muted") else "Mute")
            entry["mute_btn"].setStyleSheet("background-color: #f7768e; color: #15161e; font-weight: bold;" if s.get("muted") else "")
            entry["mute_btn"].blockSignals(False)

        # Remove dead sink widgets
        dead_ids = [sid for sid in self.sink_widgets if sid not in current_sink_ids]
        for sid in dead_ids:
            w = self.sink_widgets.pop(sid)
            w["widget"].deleteLater()

    def _on_sink_slider_moved(self, sink_id: int, val: int, label: QLabel):
        label.setText(f"{val}%")
        self.slider_timer.stop()
        self.slider_timer.timeout.disconnect() if self.slider_timer.receivers(self.slider_timer.timeout) > 0 else None
        self.slider_timer.timeout.connect(lambda: self.backend.set_volume(sink_id, val / 100.0))
        self.slider_timer.start(50)

    def _toggle_sink_mute(self, sink_id: int, button: QPushButton):
        is_muted = button.isChecked()
        self.backend.set_mute(sink_id, is_muted)
        button.setText("Muted" if is_muted else "Mute")
        button.setStyleSheet("background-color: #f7768e; color: #15161e; font-weight: bold;" if is_muted else "")

    def _set_default_sink_callback(self, sink_id: int):
        self.backend.set_default_sink(sink_id)
        self.refresh_all()

    def _toggle_master_switch(self, checked: bool):
        self.backend.set_alsa_switch("Master", checked, "1")
        self.btn_master_switch.setText("Unmuted" if checked else "Muted")
        self.btn_master_switch.setStyleSheet("background-color: #06d6a0; color: #15161e; font-weight: bold;" if checked else "background-color: #f7768e; color: #15161e; font-weight: bold;")

    def _change_master_volume(self, val: int):
        self.lbl_master_val.setText(f"{val}%")
        self.backend.set_alsa_volume("Master", val, "1")

    def _toggle_speaker_switch(self, checked: bool):
        self.backend.set_alsa_switch("Speaker", checked, "1")
        self.btn_spk_switch.setText("Unmuted" if checked else "Muted")
        self.btn_spk_switch.setStyleSheet("background-color: #06d6a0; color: #15161e; font-weight: bold;" if checked else "background-color: #f7768e; color: #15161e; font-weight: bold;")

    def _change_speaker_volume(self, val: int):
        self.lbl_spk_val.setText(f"{val}%")
        self.backend.set_alsa_volume("Speaker", val, "1")

    def _toggle_headphone_switch(self, checked: bool):
        self.backend.set_alsa_switch("Headphone", checked, "1")
        self.btn_hp_switch.setText("Unmuted" if checked else "Muted")
        self.btn_hp_switch.setStyleSheet("background-color: #06d6a0; color: #15161e; font-weight: bold;" if checked else "background-color: #f7768e; color: #15161e; font-weight: bold;")

    def _change_headphone_volume(self, val: int):
        self.lbl_hp_val.setText(f"{val}%")
        self.backend.set_alsa_volume("Headphone", val, "1")

    def _toggle_automute_switch(self, checked: bool):
        self.backend.set_alsa_auto_mute(checked, "1")
        self.btn_automute.setText(
            "Enabled (Headphone Jack Mutes Speakers)" if checked
            else "Disabled (Simultaneous Speakers & HP Allowed)"
        )

    def _unmute_both_alsa(self):
        self.backend.set_alsa_switch("Master", True, "1")
        self.backend.set_alsa_switch("Speaker", True, "1")
        self.backend.set_alsa_switch("Headphone", True, "1")
        self.backend.set_alsa_auto_mute(False, "1")
        self._refresh_hardware_and_sinks()

    def _speaker_only(self):
        self.backend.set_alsa_switch("Master", True, "1")
        self.backend.set_alsa_switch("Speaker", True, "1")
        self.backend.set_alsa_switch("Headphone", False, "1")
        self._refresh_hardware_and_sinks()

    def _headphone_only(self):
        self.backend.set_alsa_switch("Master", True, "1")
        self.backend.set_alsa_switch("Speaker", False, "1")
        self.backend.set_alsa_switch("Headphone", True, "1")
        self._refresh_hardware_and_sinks()

    def _mute_all_alsa(self):
        self.backend.set_alsa_switch("Speaker", False, "1")
        self.backend.set_alsa_switch("Headphone", False, "1")
        self._refresh_hardware_and_sinks()

    def _apply_auto_routing_now(self):
        ok, msg = self.backend.apply_audio_routing()
        self._refresh_hardware_and_sinks()
        self._refresh_header_and_overview()

    def _on_simultaneous_output_toggled(self, checked: bool):
        self.backend.set_simultaneous_output(checked)
        self._refresh_hardware_and_sinks()
        self._refresh_header_and_overview()

    def _on_mic_source_changed(self, index: int):
        target_src = self.combo_mic_source.itemData(index) or "Internal Mic"
        self.backend.set_capture_source(target_src, "1", adjust_gain=True)
        self._refresh_hardware_and_sinks()
        self._refresh_header_and_overview()

    def _on_smart_mic_switch_toggled(self, checked: bool):
        self.backend.set_smart_mic_auto_switch(checked)
        self._refresh_hardware_and_sinks()
        self._refresh_header_and_overview()

    def _toggle_capture_switch(self, checked: bool):
        self.backend.set_alsa_switch("Capture", checked, "1")
        self.btn_capture_switch.setText("Capturing" if checked else "Muted")
        self.btn_capture_switch.setStyleSheet(
            "background-color: #06d6a0; color: #15161e; font-weight: bold;"
            if checked else
            "background-color: #f7768e; color: #15161e; font-weight: bold;"
        )

    def _change_capture_volume(self, val: int):
        self.lbl_capture_val.setText(f"{val}%")
        self.backend.set_alsa_volume("Capture", val, "1")

    def _on_headset_boost_changed(self, index: int):
        lvl = self.combo_headset_boost.itemData(index)
        if lvl is not None:
            self.backend.set_alsa_boost("Mic Boost", int(lvl), "1")

    def _on_internal_boost_changed(self, index: int):
        lvl = self.combo_internal_boost.itemData(index)
        if lvl is not None:
            self.backend.set_alsa_boost("Internal Mic Boost", int(lvl), "1")

    def _check_jack_polling(self):
        """Monitor headphone jack state in real-time."""
        current_jack = self.backend.get_headphone_jack_state()
        if not hasattr(self, "_last_jack_state") or self._last_jack_state != current_jack:
            self._last_jack_state = current_jack
            self.backend.apply_audio_routing()
            self._refresh_hardware_and_sinks()
            self._refresh_header_and_overview()

    def _toggle_hdmi(self):
        hdmi_stat = self.backend.get_hdmi_status()
        enable = not hdmi_stat.get("enabled", False)
        ok, msg = self.backend.set_hdmi_enabled(enable, mode="stereo")
        if ok:
            QMessageBox.information(self, "HDMI Audio", msg)
            self.refresh_all()
        else:
            QMessageBox.critical(self, "HDMI Error", msg)

    # -------------------------------------------------------------------------
    # BLUETOOTH CONTROLS
    # -------------------------------------------------------------------------
    def _refresh_bluetooth(self):
        self.bt_list.clear()
        devices = self.backend.get_bluetooth_devices()
        for d in devices:
            conn_txt = "🟢 Connected" if d["connected"] else "⚪ Disconnected"
            bat_txt = f" [Battery: {d['battery']}%]" if d["battery"] is not None else ""
            item_text = f"<b>{d['name']}</b> ({d['mac']}) — {conn_txt}{bat_txt}"
            item = QListWidgetItem()
            item.setData(Qt.UserRole, d)

            lbl = QLabel(item_text)
            lbl.setStyleSheet("padding: 4px; color: #c0caf5;")
            self.bt_list.addItem(item)

        if not devices:
            item = QListWidgetItem("No Bluetooth audio devices currently paired or discovered.")
            self.bt_list.addItem(item)

    def _on_bt_device_selected(self):
        cur = self.bt_list.currentItem()
        if not cur or not cur.data(Qt.UserRole):
            return
        dev = cur.data(Qt.UserRole)
        # Check if card exists for device
        card = self.backend.get_bluetooth_card_for_mac(dev["mac"])
        if card:
            active_p = card.get("active_profile", "")
            if "hfp" in active_p or "headset" in active_p:
                self.combo_bt_profile.setCurrentIndex(1)
            elif "off" in active_p:
                self.combo_bt_profile.setCurrentIndex(2)
            else:
                self.combo_bt_profile.setCurrentIndex(0)

    def _connect_selected_bt(self):
        cur = self.bt_list.currentItem()
        if not cur or not cur.data(Qt.UserRole):
            QMessageBox.warning(self, "No Device", "Please select a Bluetooth device from the list.")
            return
        dev = cur.data(Qt.UserRole)
        self.status_bar_label.setText(f"Connecting to {dev['name']} ({dev['mac']})...")

        def task():
            ok, msg = self.backend.connect_bluetooth(dev["mac"])
            QtCore.QMetaObject.invokeMethod(
                self,
                "_on_bt_action_finished",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(bool, ok),
                QtCore.Q_ARG(str, f"Bluetooth Connect: {msg}")
            )
        threading.Thread(target=task, daemon=True).start()

    def _disconnect_selected_bt(self):
        cur = self.bt_list.currentItem()
        if not cur or not cur.data(Qt.UserRole):
            return
        dev = cur.data(Qt.UserRole)
        self.status_bar_label.setText(f"Disconnecting {dev['name']}...")

        def task():
            ok, msg = self.backend.disconnect_bluetooth(dev["mac"])
            QtCore.QMetaObject.invokeMethod(
                self,
                "_on_bt_action_finished",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(bool, ok),
                QtCore.Q_ARG(str, f"Bluetooth Disconnect: {msg}")
            )
        threading.Thread(target=task, daemon=True).start()

    @pyqtSlot(bool, str)
    def _on_bt_action_finished(self, ok: bool, msg: str):
        if ok:
            QMessageBox.information(self, "Bluetooth", msg)
        else:
            QMessageBox.warning(self, "Bluetooth", msg)
        self.refresh_all()

    def _set_selected_bt_as_default(self):
        cur = self.bt_list.currentItem()
        if not cur or not cur.data(Qt.UserRole):
            return
        dev = cur.data(Qt.UserRole)
        formatted_mac = dev["mac"].replace(":", "_").lower()
        status = self.backend.get_wpctl_status()
        found_sink = None
        for s in status.get("sinks", []):
            s_name = s["name"].lower()
            if formatted_mac in s_name or dev["name"].lower() in s_name or "bluez" in s_name:
                found_sink = s
                break

        if found_sink:
            self.backend.set_default_sink(found_sink["id"])
            QMessageBox.information(self, "Default Sink", f"Set {found_sink['name']} as default audio sink.")
            self.refresh_all()
        else:
            QMessageBox.warning(self, "Device Not Active", "Bluetooth device is not currently registered as an active audio sink. Connect it first.")

    def _apply_bt_profile(self):
        cur = self.bt_list.currentItem()
        if not cur or not cur.data(Qt.UserRole):
            QMessageBox.warning(self, "No Device", "Please select a Bluetooth device from the list.")
            return
        dev = cur.data(Qt.UserRole)
        idx = self.combo_bt_profile.currentIndex()
        profile_map = {0: "a2dp-sink", 1: "headset-head-unit", 2: "off"}
        target_profile = profile_map.get(idx, "a2dp-sink")

        card = self.backend.get_bluetooth_card_for_mac(dev["mac"])
        if not card:
            # Fallback to first bluez card
            for c in self.backend.get_cards():
                if "bluez" in c.get("name", ""):
                    card = c
                    break

        if card:
            ok, msg = self.backend.set_card_profile(card["name"], target_profile)
            QMessageBox.information(self, "Bluetooth Profile", msg)
            self.refresh_all()
        else:
            QMessageBox.warning(self, "No Bluetooth Audio Card", "No active Bluetooth audio card found for this device in PipeWire.")

    def _apply_bt_quality(self):
        quality = self.combo_bt_quality.currentData() or "hq"
        ok, msg = self.backend.save_wireplumber_bt_config(ldac_quality=quality, default_rate=48000, enabled=True)
        QMessageBox.information(self, "Bluetooth Bitrate Rule", msg)
        self.refresh_all()

    def _refresh_diagnostics(self):
        wp_status = self.backend.get_wpctl_status()
        meta = self.backend.get_pipewire_metadata_settings()
        pw_conf = self.backend.get_saved_pipewire_config()
        wp_conf = self.backend.get_saved_wireplumber_pro_fix()
        bt_conf = self.backend.get_saved_wireplumber_bt_config()
        alsa = self.backend.get_alsa_hardware_controls("1")

        diag = "=== PIPEWIRE METADATA SETTINGS ===\n"
        for k, v in meta.items():
            diag += f"  {k}: {v}\n"

        diag += "\n=== SAVED PIPEWIRE QUANTUM CONFIG (10-quantum.conf) ===\n"
        for k, v in pw_conf.items():
            diag += f"  {k}: {v}\n"

        diag += "\n=== SAVED WIREPLUMBER PRO AUDIO FIX (51-pro-audio-fix.conf) ===\n"
        for k, v in wp_conf.items():
            diag += f"  {k}: {v}\n"

        diag += "\n=== SAVED WIREPLUMBER BLUETOOTH BITRATE CONFIG (52-bluez-bitrate.conf) ===\n"
        for k, v in bt_conf.items():
            diag += f"  {k}: {v}\n"

        diag += "\n=== ALSA CIRRUS LOGIC CS4208 MIXER CONTROLS ===\n"
        for k, v in alsa.items():
            diag += f"  {k}: {v}\n"

        diag += "\n=== WPCTL AUDIO STATUS ===\n"
        diag += wp_status.get("raw", "None")

        self.txt_diagnostics.setText(diag)

    def _apply_preset_callback(self, preset_id: str):
        ok, logs = self.backend.apply_preset(preset_id)
        if ok:
            QMessageBox.information(self, "Preset Applied", "\n".join(logs))
            self.refresh_all()
        else:
            QMessageBox.critical(self, "Error Applying Preset", "\n".join(logs))


# =============================================================================
# CLI RUNNER & TEST SUITE
# =============================================================================

def run_tests():
    """Run comprehensive verification test suite."""
    print(">>> Running MacBook Audio Control Center Verification Suite...")
    backend = AudioBackend()

    print("[1/10] Testing PipeWire live metadata query...")
    meta = backend.get_pipewire_metadata_settings()
    assert "clock.quantum" in meta, "Missing clock.quantum"
    print(f"       OK: live quantum={meta.get('clock.quantum')}, rate={meta.get('clock.rate')}")

    print("[2/10] Testing saved PipeWire & WirePlumber config readers...")
    pw_cfg = backend.get_saved_pipewire_config()
    wp_cfg = backend.get_saved_wireplumber_pro_fix()
    bt_cfg = backend.get_saved_wireplumber_bt_config()
    print(f"       OK: pipewire 10-quantum exists={pw_cfg.get('exists')}, quantum={pw_cfg.get('quantum')}, bit_depth={pw_cfg.get('bit_depth')}")
    print(f"       OK: wireplumber 51-pro exists={wp_cfg.get('exists')}, period={wp_cfg.get('period_size')}")
    print(f"       OK: wireplumber 52-bt exists={bt_cfg.get('exists')}, quality={bt_cfg.get('ldac_quality')}")

    print("[3/10] Testing audio bitrate calculations & latency formulas...")
    kbps_48k_24b, s_48k = AudioBackend.calculate_pcm_bitrate(48000, 24, 2)
    assert kbps_48k_24b == 2304.0, f"Expected 2304 kbps, got {kbps_48k_24b}"
    kbps_96k_24b, s_96k = AudioBackend.calculate_pcm_bitrate(96000, 24, 2)
    assert kbps_96k_24b == 4608.0, f"Expected 4608 kbps, got {kbps_96k_24b}"
    lat = AudioBackend.calculate_latency_ms(1024, 48000)
    assert abs(lat - 21.33) < 0.1, f"Expected ~21.33ms, got {lat}"
    print(f"       OK: 48kHz@24bit = {s_48k}, 96kHz@24bit = {s_96k}, 1024/48k latency = {lat:.2f}ms")

    print("[4/10] Testing wpctl status parser...")
    status = backend.get_wpctl_status()
    assert len(status.get("sinks", [])) > 0, "No sinks found"
    print(f"       OK: Found {len(status['sinks'])} sinks, {len(status['sources'])} sources")

    print("[5/10] Testing audio cards & disambiguation (CS4208 vs HDMI)...")
    cards = backend.get_cards()
    assert len(cards) >= 2, f"Expected at least 2 cards, found {len(cards)}"
    cs4208_found = any("00_1b.0" in c.get("name", "") for c in cards)
    hdmi_found = any("00_03.0" in c.get("name", "") for c in cards)
    assert cs4208_found, "MacBook CS4208 card not detected"
    assert hdmi_found, "Broadwell HDMI card not detected"
    print(f"       OK: Cards validated: {[(c['description'], c['name']) for c in cards]}")

    print("[6/10] Testing Broadwell HDMI audio status & control...")
    hdmi_stat = backend.get_hdmi_status()
    assert hdmi_stat.get("card") is not None, "HDMI card mapping failed"
    print(f"       OK: HDMI card present, active profile: {hdmi_stat.get('active_profile')}")

    print("[7/10] Testing ALSA Cirrus Logic CS4208 mixer controls...")
    alsa = backend.get_alsa_hardware_controls("1")
    assert alsa.get("available"), "ALSA card 1 controls not available"
    assert "speaker_switch" in alsa and "headphone_switch" in alsa, "Missing switches"
    assert "auto_mute_mode" in alsa, "Missing auto_mute_mode"
    print(f"       OK: Master unmuted={alsa.get('master_switch')}, Speaker unmuted={alsa.get('speaker_switch')}, Auto-Mute={alsa.get('auto_mute_mode')}")

    print("[8/10] Testing Preset definitions & data validity...")
    presets = backend.get_presets()
    assert len(presets) >= 7, f"Expected at least 7 presets, got {len(presets)}"
    preset_ids = [p["id"] for p in presets]
    assert "pro_audio_fix" in preset_ids
    assert "gaming_low_latency" in preset_ids
    assert "bluetooth_hq_a2dp" in preset_ids
    print(f"       OK: {len(presets)} presets validated: {preset_ids}")

    print("[9/10] Testing Bluetooth device scanning...")
    bt_devs = backend.get_bluetooth_devices()
    print(f"       OK: Found {len(bt_devs)} paired/discovered Bluetooth devices")

    print("[10/12] Testing Headless Qt GUI Instantiation (offscreen QPA)...")
    if QT_AVAILABLE:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication.instance()
        if not app:
            app = QApplication(["--platform", "offscreen"])
        win = MainWindow(backend)
        assert win.tabs.count() == 6, f"Expected 6 tabs, found {win.tabs.count()}"
        win.show()
        print("        OK: QApplication, MainWindow, and all 6 tabs initialized cleanly in offscreen mode")
    else:
        print("        SKIP: PyQt not installed in test environment")

    print("[11/12] Testing Headphone Jack state query & Settings persistence...")
    jack = backend.get_headphone_jack_state("1")
    simul = backend.get_simultaneous_output()
    assert isinstance(jack, bool), "Jack state must be boolean"
    assert isinstance(simul, bool), "Simultaneous setting must be boolean"
    print(f"        OK: Jack state={jack}, Simultaneous output={simul} (Default is False)")

    print("[12/13] Testing Smart Exclusive Auto-Routing engine...")
    ok_route, msg_route = backend.apply_audio_routing("1")
    assert ok_route, f"Routing failed: {msg_route}"
    alsa_post = backend.get_alsa_hardware_controls("1")
    if not simul:
        if jack:
            assert not alsa_post["speaker_switch"], "Speaker should be muted when headphones plugged in"
            assert alsa_post["headphone_switch"], "Headphone should be unmuted when headphones plugged in"
        else:
            assert alsa_post["speaker_switch"], "Speaker should be unmuted when headphones unplugged"
    print(f"        OK: Auto-routing executed successfully: {msg_route}")

    print("[13/13] Testing CS4208 3.5mm Headset Microphone (Pin 0x18) & Capture Source switching...")
    ok_pin, msg_pin = backend.ensure_headset_mic_pin_enabled("1")
    assert ok_pin, f"Headset mic pin 0x18 not enabled: {msg_pin}"
    alsa_mic = backend.get_alsa_hardware_controls("1")
    assert alsa_mic.get("headset_mic_available"), "Headset Mic (Pin 0x18) + Internal Mic (Pin 0x1c) not detected in ALSA"
    assert 0 <= alsa_mic.get("mic_boost", -1) <= 3, f"Invalid mic_boost raw value: {alsa_mic.get('mic_boost')}"
    assert 0 <= alsa_mic.get("internal_mic_boost", -1) <= 2, f"Invalid internal_mic_boost raw value: {alsa_mic.get('internal_mic_boost')}"

    # Test switching to Internal Mic and Headset Mic, then restore auto-routed state
    ok_int, _ = backend.set_capture_source("Internal Mic", "1")
    assert ok_int and backend.get_capture_source("1") == "Internal Mic", "Failed to switch to Internal Mic"
    ok_ext, _ = backend.set_capture_source("Mic", "1")
    assert ok_ext and backend.get_capture_source("1") == "Mic", "Failed to switch to Headset Mic (0x18)"
    backend.apply_audio_routing("1")
    final_src = backend.get_capture_source("1")
    print(f"        OK: {msg_pin} | Active Capture Source={final_src}")

    print(">>> All 13 verification tests PASSED successfully!")
    return 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description="MacBook Audio Control Center")
    parser.add_argument("--test", "-t", action="store_true", help="Run verification tests")
    parser.add_argument("--status", "-s", action="store_true", help="Print audio status to stdout")
    parser.add_argument("--preset", "-p", type=str, help="Apply a preset by ID")
    parser.add_argument("--quantum", "-q", type=int, help="Set buffer size / quantum")
    parser.add_argument("--rate", "-r", type=int, help="Set sample rate")
    parser.add_argument("--route", action="store_true", help="Execute auto-routing check once based on jack state")
    parser.add_argument("--simultaneous", choices=["on", "off"], help="Enable or disable simultaneous multi-output")
    parser.add_argument("--mic", choices=["headset", "internal", "auto"], help="Switch microphone input ('headset', 'internal', or 'auto')")
    args = parser.parse_args()

    backend = AudioBackend()

    if args.test:
        sys.exit(run_tests())

    if args.route:
        ok, msg = backend.apply_audio_routing()
        print(msg)
        sys.exit(0 if ok else 1)

    if args.simultaneous:
        enabled = (args.simultaneous == "on")
        ok = backend.set_simultaneous_output(enabled)
        mode = "ON (Simultaneous Speakers + Headphones)" if enabled else "OFF (Exclusive Auto-Routing)"
        print(f"Simultaneous output set to {mode}")
        sys.exit(0 if ok else 1)

    if args.mic:
        if args.mic == "auto":
            backend.set_smart_mic_auto_switch(True)
            ok, msg = backend.apply_audio_routing()
            print(f"Smart Mic Auto-Switch enabled: {msg}")
            sys.exit(0 if ok else 1)
        else:
            target = "Mic" if args.mic == "headset" else "Internal Mic"
            ok, msg = backend.set_capture_source(target, "1", adjust_gain=True)
            print(msg)
            sys.exit(0 if ok else 1)

    if args.status:
        meta = backend.get_pipewire_metadata_settings()
        status = backend.get_wpctl_status()
        q = meta.get("clock.force-quantum") or meta.get("clock.quantum") or 1024
        r = meta.get("clock.force-rate") or meta.get("clock.rate") or 48000
        _, b_str = AudioBackend.calculate_pcm_bitrate(r, 24)
        jack = backend.get_headphone_jack_state("1")
        simul = backend.get_simultaneous_output()
        cap_src = backend.get_capture_source("1")
        smart_mic = backend.get_smart_mic_auto_switch()
        print(f"Active Quantum: {q}")
        print(f"Active Rate:    {r} Hz")
        print(f"Active Bitrate: {b_str}")
        def_sink = status.get("default_sink")
        print(f"Default Sink:   {def_sink['name'] if def_sink else 'None'}")
        print(f"3.5mm Jack:     {'Plugged In' if jack else 'Disconnected'}")
        print(f"Active Mic:     {'3.5mm Headset Mic (Pin 0x18)' if cap_src == 'Mic' else 'Internal Digital Mic (Pin 0x1c)'} (Auto-Switch: {'ON' if smart_mic else 'OFF'})")
        print(f"Simultaneous:   {'Enabled' if simul else 'Disabled (Default Exclusive)'}")
        sys.exit(0)

    if args.preset:
        ok, logs = backend.apply_preset(args.preset)
        for log in logs:
            print(log)
        sys.exit(0 if ok else 1)

    if args.quantum:
        ok, msg = backend.set_live_quantum(args.quantum)
        backend.save_pipewire_quantum_config(args.quantum)
        print(msg)
        sys.exit(0 if ok else 1)

    if args.rate:
        ok, msg = backend.set_live_sample_rate(args.rate)
        print(msg)
        sys.exit(0 if ok else 1)

    # Launch GUI
    if not QT_AVAILABLE:
        print("Error: PyQt5 or PyQt6 is required for GUI mode. Please install python-pyqt5 or python-pyqt6.")
        sys.exit(1)

    # Wayland display hints with fallback
    if "QT_QPA_PLATFORM" not in os.environ:
        if os.environ.get("WAYLAND_DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "wayland;xcb" if QT_VERSION == 6 else "wayland"

    try:
        app = QApplication(sys.argv)
    except Exception:
        # Fallback to xcb if native wayland platform plugin fails
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        app = QApplication(sys.argv)

    app.setApplicationName("MacBook Audio Control Center")
    app.setDesktopFileName("macbook-audio-control")

    window = MainWindow(backend)
    window.show()
    sys.exit(app.exec_() if QT_VERSION == 5 else app.exec())


if __name__ == "__main__":
    main()
