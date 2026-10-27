# MacBook Audio Control & AudioRelay Operational Rules

## 1. PipeWire & Wayland Desktop Portal Synchronization
* **NEVER restart PipeWire user daemons** (`pipewire`, `pipewire-pulse`, `wireplumber`) without immediately restarting downstream desktop portals:
  ```bash
  systemctl --user restart xdg-desktop-portal-hyprland xdg-desktop-portal
  ```
* **Why**: Restarting PipeWire severs D-Bus IPC sockets for running portal instances. This immediately breaks screen sharing across Zoom, Google Meet, and OBS with 25-second D-Bus timeouts.
* **WebRTC Tab Reload**: If Chromium or Brave was open during an audio daemon restart, reload the browser tab to re-initialize WebRTC audio/video handles.

## 2. V4L2 Hardware Camera Concurrency & Multi-Casting
* **Single-Consumer Limitation**: Linux V4L2 hardware camera nodes (`/dev/video0`, Apple FaceTime HD) cannot be opened by multiple applications simultaneously. Opening it while locked returns `EBUSY` (`Device or resource busy`).
* **Correct Multi-Casting Pattern**:
  1. Let OBS Studio capture the hardware camera `/dev/video0`.
  2. In OBS, click **"Start Virtual Camera"** to output frames to `/dev/video10` (`OBS Virtual Camera`).
  3. Meeting applications (Zoom, Google Meet, Discord) must select **"OBS Virtual Camera"** (`/dev/video10`).

## 3. PipeWire Persistent Routing vs. Ephemeral Links
* **Avoid `pw-link` for Application Routing**: `pw-link` commands are transient. As soon as an application, device, or stream reconnects, WirePlumber tears down manual links and falls back to default policies.
* **Persistent Routing**:
  * Set the system default recording source:
    ```bash
    wpctl set-default <Virtual-Mic-Node-ID>
    ```
  * Move active streams and persist preferences in WirePlumber:
    ```bash
    pactl move-source-output <stream-id> <target-source-id>
    ```

## 4. AudioRelay Networking & Subnet Invariants
* **Sender IP Verification**: AudioRelay drops incoming UDP audio packets if the packet's source IP does not match the server IP configured in the desktop client (`[WARN] Ignoring packet received from X, expected Y`).
* **Wi-Fi vs. Tailscale/VPN**: When the phone and PC are connected to the same local Wi-Fi network, always connect to the phone's physical LAN IP (e.g. `10.143.204.45`). Do not use the Tailscale/VPN IP (`100.x.x.x`), as Android will route outgoing UDP packets over the physical Wi-Fi interface, triggering packet rejection and causing buffer/latency to stay at 0.
* **UFW Firewall**: Ensure the active local Wi-Fi subnet is allowed for AudioRelay's dynamic UDP ports (59100–59200):
  ```bash
  sudo ufw allow from 10.143.204.0/24 comment "Current Local Wi-Fi Subnet"
  ```

## 5. AudioRelay Device Refresh & Session Limits
* **Refresh Button Behavior**: In AudioRelay desktop (Player tab), clicking "Refresh audio devices" immediately halts active playback. Re-check that the "Audio device" dropdown remains on **`Virtual-Mic-Sink`** (and has not reverted to "Default") before reconnecting to the phone server.
* **Free 60-Minute Timeout**: AudioRelay Free sessions automatically stop after 1 hour (`[INFO] Stopping the player, reason: unknown`). To resume, simply tap the phone server in AudioRelay to reconnect.

## 6. CS4208 3.5mm TRRS Combo Jack & Microphone Auto-Routing Invariants
* **Never Auto-Switch Capture to Pin 0x18 on Aux Headphone Insertion**:
  * On Intel MacBooks with Cirrus Logic CS4208 (MacBook Air 7,2 / 6,x, MacBook Pro 11,x), Apple routes the 3.5mm TRRS mic line through a proprietary analog multiplexer unsupported by the Linux kernel (`mba6_pincfgs` explicitly marks Pin `0x18` as disconnected `0x43ab9030`).
  * `smart_mic_auto_switch` must remain **`false`** by default in `settings.json`, `audio_backend.py`, and `audio-watchdog-rs`.
  * When aux headphones are connected, always keep the **Built-in Digital Microphone (`Internal Mic`, Pin `0x1c`)** active. This ensures voice capture remains 100% operational in Zoom, WhatsApp, Discord, and browsers while listening through headphones.
* **Open-Circuit Thermal Float vs. Acoustic Signal**:
  * Do NOT rely solely on raw RMS energy when evaluating unmasked HDA pins. At +22 dB gain (+10 dB Mic Boost + +12 dB ADC gain), an open-circuit, disconnected analog pin floating at -58 dBFS will register an RMS of 500–1000 purely from thermal Johnson noise and 50 Hz power hum.
  * Always verify acoustic responsiveness using Goertzel/FFT frequency profile analysis (300 Hz – 3 kHz vocal band) and sample-to-sample delta distributions.
* **External Headset Microphone Standard**:
  * For users requiring an external physical boom microphone, advise using a standard USB-to-3.5mm adapter (or USB headset). USB Audio Class controllers bypass Apple's proprietary analog multiplexer entirely and work natively without kernel patching.
