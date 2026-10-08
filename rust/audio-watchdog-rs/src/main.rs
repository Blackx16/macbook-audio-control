use std::env;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

extern "C" {
    fn kill(pid: i32, sig: i32) -> i32;
    fn signal(signum: i32, handler: usize) -> usize;
}

const SIGTERM: i32 = 15;
const SIGINT: i32 = 2;

fn dirs_home() -> PathBuf {
    env::var("HOME").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from("."))
}

fn get_app_dir() -> PathBuf {
    let mut p = dirs_home();
    p.push(".local");
    p.push("share");
    p.push("macbook-audio-control");
    let _ = fs::create_dir_all(&p);
    p
}

/// Query whether the 3.5mm headphone jack is plugged in on ALSA Card 1 (CS4208)
fn query_headphone_jack_state() -> bool {
    let output = Command::new("amixer")
        .args(&["-c", "1", "cget", "iface=CARD,name=Headphone Jack"])
        .output();

    if let Ok(out) = output {
        let stdout = String::from_utf8_lossy(&out.stdout);
        if stdout.contains("values=on") {
            return true;
        }
        if stdout.contains("values=off") {
            return false;
        }
    }

    // Fallback: try numid=14
    let output2 = Command::new("amixer")
        .args(&["-c", "1", "cget", "numid=14"])
        .output();

    if let Ok(out) = output2 {
        let stdout = String::from_utf8_lossy(&out.stdout);
        if stdout.contains("values=on") {
            return true;
        }
    }

    false
}

/// Set ALSA mixer switch (e.g. "Speaker", "Headphone") to mute or unmute
fn set_alsa_switch(name: &str, unmute: bool) {
    let state = if unmute { "unmute" } else { "mute" };
    let _ = Command::new("amixer")
        .args(&["-c", "1", "set", name, state])
        .output();
}

/// Read whether simultaneous multi-audio output is enabled in settings.json (default false)
fn read_simultaneous_setting(settings_file: &PathBuf) -> bool {
    if let Ok(content) = fs::read_to_string(settings_file) {
        // Simple JSON parse: look for "simultaneous_output": true
        let lower = content.to_lowercase();
        if lower.contains("\"simultaneous_output\": true") || lower.contains("\"simultaneous_output\":true") {
            return true;
        }
    }
    false
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let app_dir = get_app_dir();
    let pid_file = app_dir.join("watchdog.pid");
    let status_file = app_dir.join("watchdog_status.json");
    let log_file = app_dir.join("watchdog.log");
    let settings_file = app_dir.join("settings.json");

    if args.len() > 1 {
        match args[1].as_str() {
            "--status" => {
                if let Ok(content) = fs::read_to_string(&status_file) {
                    println!("{}", content);
                } else {
                    let jack = query_headphone_jack_state();
                    let simultaneous = read_simultaneous_setting(&settings_file);
                    println!(
                        "{{\"running\":false,\"xruns\":0,\"last_xrun\":\"never\",\"jack_plugged\":{},\"simultaneous_output\":{}}}",
                        jack, simultaneous
                    );
                }
                return;
            }
            "--stop" => {
                if let Ok(pid_str) = fs::read_to_string(&pid_file) {
                    if let Ok(pid) = pid_str.trim().parse::<i32>() {
                        unsafe {
                            kill(pid, SIGTERM);
                        }
                        println!("{{\"stopped\":true,\"pid\":{}}}", pid);
                        let _ = fs::remove_file(&pid_file);
                        let _ = fs::write(&status_file, "{\"running\":false,\"xruns\":0,\"last_xrun\":\"stopped\"}");
                        return;
                    }
                }
                println!("{{\"stopped\":false,\"reason\":\"No active daemon found\"}}");
                return;
            }
            "--daemon" => {
                run_daemon(&pid_file, &status_file, &log_file, &settings_file);
                return;
            }
            "--route-now" => {
                // One-shot routing update
                let simultaneous = read_simultaneous_setting(&settings_file);
                let jack = query_headphone_jack_state();
                if simultaneous {
                    set_alsa_switch("Speaker", true);
                    set_alsa_switch("Headphone", true);
                    println!("{{\"mode\":\"simultaneous\",\"speaker\":true,\"headphone\":true}}");
                } else if jack {
                    set_alsa_switch("Speaker", false);
                    set_alsa_switch("Headphone", true);
                    println!("{{\"mode\":\"exclusive_headphone\",\"speaker\":false,\"headphone\":true,\"jack\":true}}");
                } else {
                    set_alsa_switch("Speaker", true);
                    println!("{{\"mode\":\"exclusive_speaker\",\"speaker\":true,\"jack\":false}}");
                }
                return;
            }
            "-h" | "--help" => {
                println!("audio-watchdog-rs - Real-time PipeWire audio dropout & Headphone Jack Auto-Routing daemon");
                println!("Usage:");
                println!("  audio-watchdog-rs --daemon     # Run daemon monitoring dropouts & jack auto-routing");
                println!("  audio-watchdog-rs --status     # Check daemon status, jack status, and XRUN count");
                println!("  audio-watchdog-rs --route-now  # Apply immediate jack routing check once");
                println!("  audio-watchdog-rs --stop       # Stop running watchdog daemon");
                return;
            }
            _ => {}
        }
    }

    // Default: run foreground daemon
    run_daemon(&pid_file, &status_file, &log_file, &settings_file);
}

static TERMINATE_REQUESTED: AtomicBool = AtomicBool::new(false);

extern "C" fn handle_sigterm(_sig: i32) {
    TERMINATE_REQUESTED.store(true, Ordering::SeqCst);
}

fn run_daemon(pid_file: &PathBuf, status_file: &PathBuf, log_file: &PathBuf, settings_file: &PathBuf) {
    let my_pid = std::process::id();
    let _ = fs::write(pid_file, format!("{}\n", my_pid));

    // Setup signal handler
    unsafe {
        signal(SIGTERM, handle_sigterm as *const () as usize);
        signal(SIGINT, handle_sigterm as *const () as usize);
    }

    let initial_jack = query_headphone_jack_state();
    let initial_simul = read_simultaneous_setting(settings_file);

    let _ = fs::write(
        status_file,
        format!(
            "{{\"running\":true,\"pid\":{},\"xruns\":0,\"last_xrun\":null,\"jack_plugged\":{},\"simultaneous_output\":{}}}",
            my_pid, initial_jack, initial_simul
        ),
    );

    println!(
        "Audio Watchdog started (PID {}). Monitoring PipeWire XRUNs and Jack auto-routing...",
        my_pid
    );

    // Spawn background Jack Sensing & Exclusive Routing thread
    let settings_path_clone = settings_file.clone();
    let status_path_clone = status_file.clone();
    let jack_thread = thread::spawn(move || {
        let mut last_jack: Option<bool> = None;
        let mut last_simul: Option<bool> = None;

        while !TERMINATE_REQUESTED.load(Ordering::Relaxed) {
            let simultaneous = read_simultaneous_setting(&settings_path_clone);
            let jack_state = query_headphone_jack_state();

            let state_changed = match (last_jack, last_simul) {
                (Some(lj), Some(ls)) => lj != jack_state || ls != simultaneous,
                _ => true,
            };

            if state_changed {
                last_jack = Some(jack_state);
                last_simul = Some(simultaneous);

                if simultaneous {
                    // User explicitly turned on Simultaneous Multi-Audio Output:
                    set_alsa_switch("Speaker", true);
                    set_alsa_switch("Headphone", true);
                } else {
                    // Default Exclusive Auto-Routing:
                    if jack_state {
                        // 3.5mm Headphone Jack is plugged in:
                        // Output routes EXCLUSIVELY to Headphones (internal speakers muted)
                        set_alsa_switch("Speaker", false);
                        set_alsa_switch("Headphone", true);
                    } else {
                        // 3.5mm Headphone Jack is unplugged:
                        // Output routes EXCLUSIVELY to internal speakers
                        set_alsa_switch("Speaker", true);
                    }
                }

                // Update status JSON with current jack & routing state
                if let Ok(content) = fs::read_to_string(&status_path_clone) {
                    if let Ok(val) = serde_json_lite_replace(
                        &content,
                        jack_state,
                        simultaneous,
                    ) {
                        let _ = fs::write(&status_path_clone, val);
                    }
                }
            }

            thread::sleep(Duration::from_millis(500));
        }
    });

    // Monitor PipeWire journal for audio dropouts
    let mut journal_proc: Option<Child> = Command::new("journalctl")
        .args(&["--user", "-u", "pipewire", "-f", "-n", "0"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok();

    let mut xrun_count: u64 = 0;

    if let Some(ref mut child) = journal_proc {
        if let Some(stdout) = child.stdout.take() {
            let reader = BufReader::new(stdout);
            for line_res in reader.lines() {
                if TERMINATE_REQUESTED.load(Ordering::Relaxed) {
                    break;
                }
                if let Ok(line) = line_res {
                    if line.contains("Broken pipe")
                        || line.contains("snd_pcm_avail after recover")
                        || line.contains("xrun")
                        || line.contains("underrun")
                    {
                        xrun_count += 1;
                        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();

                        // Log event
                        if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(log_file) {
                            let _ = writeln!(f, "[timestamp={}] XRUN detected: {}", now, line);
                        }

                        let jack_curr = query_headphone_jack_state();
                        let simul_curr = read_simultaneous_setting(settings_file);

                        // Update status JSON
                        let _ = fs::write(
                            status_file,
                            format!(
                                "{{\"running\":true,\"pid\":{},\"xruns\":{},\"last_xrun\":{},\"jack_plugged\":{},\"simultaneous_output\":{}}}",
                                my_pid, xrun_count, now, jack_curr, simul_curr
                            ),
                        );

                        // Protect quantum floor
                        let _ = Command::new("pw-metadata")
                            .args(&["-n", "settings", "0", "clock.min-quantum", "512"])
                            .status();

                        // Notification
                        let _ = Command::new("notify-send")
                            .args(&[
                                "-u",
                                "low",
                                "-a",
                                "Audio Watchdog",
                                "Audio Dropout Detected",
                                "Audio buffer underrun mitigated by PipeWire watchdog.",
                            ])
                            .status();
                    }
                }
            }
        }
        let _ = child.kill();
    }

    TERMINATE_REQUESTED.store(true, Ordering::SeqCst);
    let _ = jack_thread.join();

    // Cleanup on exit
    let _ = fs::remove_file(pid_file);
    let final_jack = query_headphone_jack_state();
    let final_simul = read_simultaneous_setting(settings_file);
    let _ = fs::write(
        status_file,
        format!(
            "{{\"running\":false,\"xruns\":{},\"last_xrun\":\"terminated\",\"jack_plugged\":{},\"simultaneous_output\":{}}}",
            xrun_count, final_jack, final_simul
        ),
    );
    println!("Audio Watchdog stopped cleanly.");
}

fn serde_json_lite_replace(content: &str, jack: bool, simultaneous: bool) -> Result<String, ()> {
    // Quick in-place update for jack_plugged and simultaneous_output fields
    let mut updated = content.to_string();
    if updated.contains("\"jack_plugged\":true") {
        updated = updated.replace("\"jack_plugged\":true", &format!("\"jack_plugged\":{}", jack));
    } else if updated.contains("\"jack_plugged\":false") {
        updated = updated.replace("\"jack_plugged\":false", &format!("\"jack_plugged\":{}", jack));
    }

    if updated.contains("\"simultaneous_output\":true") {
        updated = updated.replace("\"simultaneous_output\":true", &format!("\"simultaneous_output\":{}", simultaneous));
    } else if updated.contains("\"simultaneous_output\":false") {
        updated = updated.replace("\"simultaneous_output\":false", &format!("\"simultaneous_output\":{}", simultaneous));
    }

    Ok(updated)
}
