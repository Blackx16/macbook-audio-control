use std::env;

fn main() {
    let args: Vec<String> = env::args().collect();
    let mut card = "1";

    let mut i = 1;
    let mut cmd = "all";
    let mut target_name = "";
    let mut target_val = "";

    while i < args.len() {
        match args[i].as_str() {
            "--card" => {
                if i + 1 < args.len() {
                    card = &args[i + 1];
                    i += 1;
                }
            }
            "--alsa" => cmd = "alsa",
            "--pw" => cmd = "pw",
            "--all" => cmd = "all",
            "--set-vol" => {
                cmd = "set-vol";
                if i + 2 < args.len() {
                    target_name = &args[i + 1];
                    target_val = &args[i + 2];
                    i += 2;
                }
            }
            "--set-switch" => {
                cmd = "set-switch";
                if i + 2 < args.len() {
                    target_name = &args[i + 1];
                    target_val = &args[i + 2];
                    i += 2;
                }
            }
            "--set-auto-mute" => {
                cmd = "set-auto-mute";
                if i + 1 < args.len() {
                    target_val = &args[i + 1];
                    i += 1;
                }
            }
            "-h" | "--help" => {
                println!("audio-query-rs - Fast ALSA & PipeWire query tool");
                println!("Usage:");
                println!("  audio-query-rs [--card 1] [--all|--alsa|--pw]");
                println!("  audio-query-rs --set-vol <Control> <0-100>");
                println!("  audio-query-rs --set-switch <Control> <0|1>");
                println!("  audio-query-rs --set-auto-mute <0|1>");
                return;
            }
            _ => {}
        }
        i += 1;
    }

    match cmd {
        "alsa" => {
            println!("{}", audio_query::build_alsa_json(card));
        }
        "pw" => {
            let pw = audio_query::pipewire::query_pipewire();
            println!("{:?}", pw);
        }
        "set-vol" => {
            if let Ok(pct) = target_val.parse::<i32>() {
                if let Ok(mixer) = audio_query::alsa::MixerHandle::open(card) {
                    match mixer.set_volume(target_name, pct) {
                        Ok(_) => println!("{{\"status\":\"ok\",\"set_volume\":\"{}\",\"val\":{}}}", target_name, pct),
                        Err(e) => eprintln!("{{\"status\":\"error\",\"code\":{}}}", e),
                    }
                }
            }
        }
        "set-switch" => {
            let unmute = target_val == "1" || target_val.eq_ignore_ascii_case("true") || target_val.eq_ignore_ascii_case("unmute");
            if let Ok(mixer) = audio_query::alsa::MixerHandle::open(card) {
                match mixer.set_switch(target_name, unmute) {
                    Ok(_) => println!("{{\"status\":\"ok\",\"set_switch\":\"{}\",\"unmuted\":{}}}", target_name, unmute),
                    Err(e) => eprintln!("{{\"status\":\"error\",\"code\":{}}}", e),
                }
            }
        }
        "set-auto-mute" => {
            let enable = target_val == "1" || target_val.eq_ignore_ascii_case("true") || target_val.eq_ignore_ascii_case("enabled");
            if let Ok(mixer) = audio_query::alsa::MixerHandle::open(card) {
                match mixer.set_auto_mute_mode(enable) {
                    Ok(_) => println!("{{\"status\":\"ok\",\"set_auto_mute\":{}}}", enable),
                    Err(e) => eprintln!("{{\"status\":\"error\",\"code\":{}}}", e),
                }
            }
        }
        _ => {
            println!("{}", audio_query::build_all_json(card));
        }
    }
}
