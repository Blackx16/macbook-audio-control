use std::process::Command;

#[derive(Default, Debug)]
pub struct PipeWireStatus {
    pub quantum: u32,
    pub rate: u32,
    pub min_quantum: u32,
    pub max_quantum: u32,
    pub default_sink: String,
    pub default_source: String,
    pub sinks: Vec<AudioNode>,
    pub sources: Vec<AudioNode>,
}

#[derive(Default, Debug)]
pub struct AudioNode {
    pub id: u32,
    pub name: String,
    pub volume: f32,
    pub is_default: bool,
}

pub fn query_pipewire() -> PipeWireStatus {
    let mut status = PipeWireStatus {
        quantum: 1024,
        rate: 48000,
        min_quantum: 512,
        max_quantum: 2048,
        ..Default::default()
    };

    // 1. Query metadata
    if let Ok(output) = Command::new("pw-metadata").arg("-n").arg("settings").output() {
        if let Ok(text) = String::from_utf8(output.stdout) {
            for line in text.lines() {
                if line.contains("clock.quantum") && !line.contains("min-") && !line.contains("max-") && !line.contains("force-") {
                    if let Some(val) = extract_metadata_val(line) {
                        if let Ok(num) = val.parse::<u32>() {
                            status.quantum = num;
                        }
                    }
                } else if line.contains("clock.rate") && !line.contains("allowed-") && !line.contains("force-") {
                    if let Some(val) = extract_metadata_val(line) {
                        if let Ok(num) = val.parse::<u32>() {
                            status.rate = num;
                        }
                    }
                } else if line.contains("clock.min-quantum") {
                    if let Some(val) = extract_metadata_val(line) {
                        if let Ok(num) = val.parse::<u32>() {
                            status.min_quantum = num;
                        }
                    }
                } else if line.contains("clock.max-quantum") {
                    if let Some(val) = extract_metadata_val(line) {
                        if let Ok(num) = val.parse::<u32>() {
                            status.max_quantum = num;
                        }
                    }
                }
            }
        }
    }

    // 2. Query wpctl status
    if let Ok(output) = Command::new("wpctl").arg("status").output() {
        if let Ok(text) = String::from_utf8(output.stdout) {
            let mut section = "";
            for line in text.lines() {
                if line.contains("Sinks:") {
                    section = "sinks";
                    continue;
                } else if line.contains("Sources:") {
                    section = "sources";
                    continue;
                } else if line.contains("Filters:") || line.contains("Streams:") || line.contains("Video") {
                    section = "";
                    continue;
                }

                if section == "sinks" || section == "sources" {
                    let trimmed = line.trim();
                    if trimmed.is_empty() {
                        continue;
                    }

                    let is_default = trimmed.contains('*');
                    let clean = trimmed.replace('*', "");
                    let parts: Vec<&str> = clean.split('.').collect();
                    if parts.len() >= 2 {
                        if let Ok(id) = parts[0].trim().parse::<u32>() {
                            let rest = parts[1..].join(".");
                            let name_and_vol: Vec<&str> = rest.split('[').collect();
                            let name = name_and_vol[0].trim().to_string();

                            let mut vol = 1.0f32;
                            if name_and_vol.len() > 1 {
                                let vol_part = name_and_vol[1].replace(']', "");
                                if let Some(idx) = vol_part.find("vol:") {
                                    let v_str = vol_part[idx + 4..].trim();
                                    if let Ok(v) = v_str.parse::<f32>() {
                                        vol = v;
                                    }
                                }
                            }

                            let node = AudioNode {
                                id,
                                name: name.clone(),
                                volume: vol,
                                is_default,
                            };

                            if section == "sinks" {
                                if is_default {
                                    status.default_sink = name;
                                }
                                status.sinks.push(node);
                            } else {
                                if is_default {
                                    status.default_source = name;
                                }
                                status.sources.push(node);
                            }
                        }
                    }
                }
            }
        }
    }

    status
}

fn extract_metadata_val(line: &str) -> Option<&str> {
    if let Some(idx) = line.find("value:'") {
        let after = &line[idx + 7..];
        if let Some(end_idx) = after.find('\'') {
            return Some(&after[..end_idx]);
        }
    }
    None
}
