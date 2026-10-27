# Complete Guide: AudioRelay Virtual Microphone, PipeWire, Screen Sharing & V4L2 Camera on Linux / Hyprland

This document provides a comprehensive post-mortem, system architecture guide, diagnosis log, and permanent operational checklist for running **AudioRelay**, a **PipeWire Virtual Microphone**, **Wayland Screen Sharing**, and **OBS/V4L2 Camera** on Arch Linux / CachyOS (MacBookAir7,2 / CS4208 audio codec).

---

## 1. System Environment & Hardware Context

* **Machine**: Apple MacBook Air 13" Early 2015 (`MacBookAir7,2`)
* **Audio Codec**: Cirrus Logic CS4208 (HDA Intel PCH, PCI `0000:00:1b.0`) running in `pro-audio` profile
* **OS / Kernel**: CachyOS (Arch-based), Linux Kernel `7.3.0`
* **Display Server**: Wayland / Hyprland compositor
* **Audio Server**: PipeWire `1.6.10` + WirePlumber + `pipewire-pulse`
* **Audio Watchdog Daemon**: `audio-watchdog-rs` (systemd user unit `macbook-audio-watchdog.service`)
* **Hardware Camera**: Apple FaceTime HD Camera (PCI `0000:02:00.0`, `/dev/video0`)
* **Virtual Camera**: `v4l2loopback` device node `/dev/video10`
* **Target Software**: AudioRelay `0.27.5`, Zoom, Google Meet (Brave/Chromium), OBS Studio

---

## 2. Issues Diagnosed, Root Causes & Fixes

### Issue 1: Wayland / Hyprland Screen Sharing Broken ("Can't share screen")

#### Symptoms
* In Zoom, Google Meet (Brave), or OBS, clicking "Share Screen" either failed silently or hung for 25 seconds before giving an error.
* OBS logs showed:
  ```text
  [pipewire] Failed to close session implementation: Timeout was reached
  ```

#### Root Cause
In Hyprland/Wayland, screen sharing requests travel through D-Bus:
$$\text{Application} \longrightarrow \text{xdg-desktop-portal} \longrightarrow \text{xdg-desktop-portal-hyprland} \longrightarrow \text{PipeWire Screencast Stream}$$

When `pipewire` or `pipewire-pulse` was restarted earlier, the already-running portal services (`xdg-desktop-portal` and `xdg-desktop-portal-hyprland`) retained dead PipeWire IPC sockets. Every new screencast request hung waiting on dead sockets and failed with a 25-second D-Bus timeout.

#### Fix Applied
Restart both user portal services whenever PipeWire is restarted:
```bash
systemctl --user restart xdg-desktop-portal-hyprland xdg-desktop-portal
```

#### Rule / Takeaway
> **Golden Rule**: Never restart PipeWire user daemons in Wayland without immediately restarting `xdg-desktop-portal-hyprland` and `xdg-desktop-portal`. If a browser was open during the restart, reload the browser tab so Chromium/Brave reconnects its WebRTC portal handles.

---

### Issue 2: OBS Camera Not Working ("Device or resource busy")

#### Symptoms
* In OBS Studio, the video capture device remained black.
* OBS terminal output showed:
  ```text
  v4l2-input: Initialization failed, errno: Device or resource busy
  ```

#### Root Cause
Linux V4L2 device drivers for the FaceTime HD camera (`/dev/video0`) do not allow multiple applications to acquire direct exclusive hardware locks simultaneously. 

Zoom (`/opt/zoom/zoom`, PID 755275) was running in the background and holding an exclusive `ioctl` lock on `/dev/video0` (`fuser /dev/video0` reported Zoom PID). When OBS attempted to open `/dev/video0`, the kernel returned `EBUSY`.

#### Architecture Solution
To use your camera in both OBS and meeting apps (Zoom / Meet):
1. **OBS Captures Hardware**: Allow OBS to be the sole consumer of `/dev/video0` (FaceTime HD camera).
2. **OBS Virtual Camera**: In OBS, click **"Start Virtual Camera"**. This writes video frames to the loopback device node `/dev/video10` (`OBS Virtual Camera`).
3. **Meeting Apps Use Virtual Camera**: In Zoom, Google Meet, or Discord, select **"OBS Virtual Camera"** (`/dev/video10`) instead of the raw FaceTime HD camera.

---

### Issue 3: Virtual Microphone Pipeline Architecture & Isolation

#### Requirement
* Allow AudioRelay on Android to send microphone audio over the network to the PC.
* The PC must feed this audio into communication apps (Zoom, Meet, Discord, OBS) as a virtual microphone input.
* **Critical Requirement**: Zero audio feedback/leakage into the physical MacBook speakers or headphones.

#### Implementation Architecture
Created a PipeWire loopback module configuration file:
**File path**: `~/.config/pipewire/pipewire.conf.d/20-audiorelay-virtual-mic.conf`

```spa
# Generated for AudioRelay Virtual Microphone Pipeline
context.modules = [
    { name = libpipewire-module-loopback
      args = {
          node.description = "AudioRelay Virtual Microphone"
          capture.props = {
              node.name = "audiorelay-virtual-mic-sink"
              node.description = "Virtual-Mic-Sink"
              media.class = "Audio/Sink"
              audio.position = [ FL FR ]
          }
          playback.props = {
              node.name = "Virtual-Mic"
              node.description = "Virtual-Mic"
              media.class = "Audio/Source"
              audio.position = [ FL FR ]
          }
      }
    }
]
```

#### How it Works:
1. `audiorelay-virtual-mic-sink` acts as an **Audio/Sink** (playback target). AudioRelay's Player connects here.
2. The loopback module internally transfers audio from `audiorelay-virtual-mic-sink` to `Virtual-Mic`.
3. `Virtual-Mic` acts as an **Audio/Source** (recording input). Apps (Zoom, OBS, Meet) capture from this source.
4. Because `audiorelay-virtual-mic-sink` is NOT linked to `alsa_output.pci-0000_00_1b.0.pro-output-0`, no sound leaks into your physical speakers or headphones.

---

### Issue 4: Virtual Mic "Dying" on Device Refresh in AudioRelay

#### Symptoms
* In AudioRelay, clicking the "Refresh" button next to Audio Device caused the virtual microphone to stop responding in Zoom/OBS.

#### Root Causes Identified & Resolved

1. **AudioRelay Stops Player on Refresh**:
   * Clicking the circular "Refresh" button in AudioRelay immediately terminates the active streaming connection (`[INFO] Stopping the player, reason: unknown`). AudioRelay does not automatically resume; you must click on your phone server again to start streaming.
2. **Dropdown Reverts to "Default"**:
   * Re-enumerating devices in AudioRelay can cause the "Audio Device" dropdown to jump from `Virtual-Mic-Sink` back to `Default` (which routes audio to laptop speakers, starving `Virtual-Mic`).
3. **WirePlumber Saved 11% Volume (-57.5 dB)**:
   * WirePlumber had previously cached `channelVolumes: [0.001331, 0.001331]` in `~/.local/state/wireplumber/stream-properties`, which effectively muted `Virtual-Mic-Sink` upon creation.
   * **Fix**: Restored volume to 100% and updated WirePlumber persistent state:
     ```bash
     wpctl set-volume 38 1.0
     pactl set-sink-volume audiorelay-virtual-mic-sink 100%
     ```
4. **WirePlumber Default Source Was Falling Back to Internal Mic**:
   * `Virtual-Mic` was not marked as default in WirePlumber (`default.configured.audio.source` was still `alsa_input...`). Any app without an explicit binding switched back to the laptop mic.
   * **Fix**: Permanently registered `Virtual-Mic` as default source:
     ```bash
     wpctl set-default <Virtual-Mic-ID>
     ```
5. **Transient `pw-link` Replaced with Persistent Stream Restoration**:
   * Ephemeral `pw-link` commands break when streams restart. Used `pactl move-source-output <output-id> <Virtual-Mic-ID>` so WirePlumber permanently restores future streams to `Virtual-Mic`.
6. **AudioRelay 60-Minute Session Timeout**:
   * AudioRelay Free has an intentional 60-minute session cap (`00:27:52 -> 01:27:52: [INFO] Stopping the player, reason: unknown`). When the hour expires, tap your phone in AudioRelay to reconnect.

---

### Issue 5: AudioRelay Buffer & Latency Stuck at 0 ms (IP & Subnet Mismatch)

#### Symptoms
* The user entered `100.93.248.145` (Tailscale / VPN IP) in AudioRelay.
* AudioRelay showed **Buffer: 0 ms**, **Latency: 0 ms**, and no sound was received.

#### Root Cause
Inspecting `~/.cache/audiorelay/logs/audiorelay.log` revealed:
```text
[INFO] Remotely connecting to 100.93.248.145...
[INFO] Received server config, server: 0.26.1, os: ANDROID, osVersion: Android API 37
[WARN] Ignoring packet received from 10.143.204.45, expected 100.93.248.145
```

1. Both the phone and the MacBook were connected to the **same local Wi-Fi network** (`10.143.204.0/24`).
   * MacBook Wi-Fi IP: `10.143.204.206`
   * Phone Wi-Fi IP: `10.143.204.45`
2. Even though the initial TCP handshake was addressed to `100.93.248.145`, the Android operating system routed outgoing UDP audio packets over its default physical Wi-Fi interface (`10.143.204.45`).
3. AudioRelay on Linux performs sender IP verification: because the UDP packet originated from `10.143.204.45` instead of `100.93.248.145`, AudioRelay discarded every packet as untrusted/unexpected.
4. With all UDP audio dropped, buffer and latency stayed at 0.

#### Fix Applied
1. **Allowed Active Wi-Fi Subnet in UFW Firewall**:
   ```bash
   sudo ufw allow from 10.143.204.0/24 comment "Current Local Wi-Fi Subnet"
   ```
2. **Configured AudioRelay with Phone's Real Wi-Fi IP**:
   * Instead of `100.93.248.145`, connect directly to the phone's LAN IP: `10.143.204.45`.
   * Verified with ping (3.5 ms latency) and TCP port check (`59100` open).

---

## 3. Verification & Diagnostic Commands

### Check PipeWire Sinks & Sources
```bash
wpctl status
pactl list sinks short
pactl list sources short
```

### Verify Default Audio Devices
```bash
pactl info | grep -E "Default Sink|Default Source"
```

### Verify Live Audio Flow through Virtual-Mic
Run this one-liner to record a sample from `Virtual-Mic` and check peak amplitude:
```bash
python3 -c "
import subprocess, time, numpy as np
proc = subprocess.Popen(['pw-record', '--raw', '--target', 'Virtual-Mic', '--format', 's16', '--rate', '48000', '--channels', '2', '-'], stdout=subprocess.PIPE)
time.sleep(1.5)
proc.terminate()
raw, _ = proc.communicate()
arr = np.frombuffer(raw, dtype=np.int16) if raw else np.array([])
print(f'Samples: {len(arr)}, Max Amplitude: {np.max(np.abs(arr)) if len(arr) > 0 else 0}')
"
```
*(Speaking into the phone should produce a Max Amplitude between 15,000 and 32,767).*

### Inspect AudioRelay Logs in Real Time
```bash
tail -f ~/.cache/audiorelay/logs/audiorelay.log
```

### Check Camera Device Locks
```bash
fuser -v /dev/video*
```

### Restart Desktop Portals (Fixes Screen Sharing)
```bash
systemctl --user restart xdg-desktop-portal-hyprland xdg-desktop-portal
```

---

## 4. Daily Operational Checklist

1. **On Android Phone**:
   * Ensure phone is on the same Wi-Fi network as the PC.
   * Open **AudioRelay** &rarr; **Server** tab &rarr; Tap **Microphone**.
   * Note the displayed IP address (e.g., `10.143.204.45`).

2. **On PC (AudioRelay Desktop)**:
   * Open **AudioRelay** &rarr; **Player** tab.
   * Verify **Audio device** is set to **`Virtual-Mic-Sink`** *(never leave it on "Default")*.
   * Connect to the phone using its **Wi-Fi IP address** (`10.143.204.45`).
   * Verify that **Buffer** and **Latency** show positive values (e.g. ~20–40 ms).

3. **In Communication Apps (Zoom, Google Meet, OBS, Discord)**:
   * Set Microphone to **`Virtual-Mic`** (or **System Default**).
   * For camera in multiple apps: Let OBS capture `/dev/video0`, click **Start Virtual Camera**, and choose **`OBS Virtual Camera`** in Zoom/Meet.
