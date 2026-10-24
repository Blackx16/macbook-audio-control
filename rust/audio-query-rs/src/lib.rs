pub mod alsa;
pub mod pipewire;

use std::ffi::{CStr, CString};
use std::os::raw::{c_char, c_int};

pub fn build_alsa_json(card: &str) -> String {
    let mixer = match alsa::MixerHandle::open(card) {
        Ok(m) => m,
        Err(e) => return format!("{{\"error\":\"Failed to open ALSA mixer on {}: {}\"}}", card, e),
    };

    let master = mixer.get_volume_and_switch("Master").unwrap_or((0, false));
    let speaker = mixer.get_volume_and_switch("Speaker").unwrap_or((0, false));
    let headphone = mixer.get_volume_and_switch("Headphone").unwrap_or((0, false));
    let capture = mixer.get_volume_and_switch("Capture").unwrap_or((0, false));
    let auto_mute = mixer.get_auto_mute_mode().unwrap_or_else(|| "Disabled".to_string());
    let mic_boost = mixer.get_raw_volume("Mic Boost").unwrap_or((0, 3));
    let internal_boost = mixer.get_raw_volume("Internal Mic Boost").unwrap_or((2, 2));
    let (capture_source, headset_mic_available) = mixer.get_capture_source();

    format!(
        "{{\"card\":\"{}\",\"master\":{{\"volume\":{},\"unmuted\":{}}},\"speaker\":{{\"volume\":{},\"unmuted\":{}}},\"headphone\":{{\"volume\":{},\"unmuted\":{}}},\"capture\":{{\"volume\":{},\"unmuted\":{}}},\"auto_mute\":\"{}\",\"mic_boost\":{{\"volume\":{},\"max\":{}}},\"internal_mic_boost\":{{\"volume\":{},\"max\":{}}},\"capture_source\":\"{}\",\"headset_mic_available\":{}}}",
        card,
        master.0, master.1,
        speaker.0, speaker.1,
        headphone.0, headphone.1,
        capture.0, capture.1,
        auto_mute,
        mic_boost.0, mic_boost.1,
        internal_boost.0, internal_boost.1,
        capture_source,
        headset_mic_available
    )
}

pub fn build_all_json(card: &str) -> String {
    let alsa_json = build_alsa_json(card);
    let pw = pipewire::query_pipewire();

    let mut sinks_json = String::from("[");
    for (i, s) in pw.sinks.iter().enumerate() {
        if i > 0 { sinks_json.push(','); }
        sinks_json.push_str(&format!(
            "{{\"id\":{},\"name\":\"{}\",\"volume\":{:.2},\"is_default\":{}}}",
            s.id, s.name.replace('"', "\\\""), s.volume, s.is_default
        ));
    }
    sinks_json.push(']');

    let mut sources_json = String::from("[");
    for (i, s) in pw.sources.iter().enumerate() {
        if i > 0 { sources_json.push(','); }
        sources_json.push_str(&format!(
            "{{\"id\":{},\"name\":\"{}\",\"volume\":{:.2},\"is_default\":{}}}",
            s.id, s.name.replace('"', "\\\""), s.volume, s.is_default
        ));
    }
    sources_json.push(']');

    format!(
        "{{\"alsa\":{},\"pipewire\":{{\"quantum\":{},\"rate\":{},\"min_quantum\":{},\"max_quantum\":{},\"default_sink\":\"{}\",\"default_source\":\"{}\",\"sinks\":{},\"sources\":{}}}}}",
        alsa_json,
        pw.quantum,
        pw.rate,
        pw.min_quantum,
        pw.max_quantum,
        pw.default_sink.replace('"', "\\\""),
        pw.default_source.replace('"', "\\\""),
        sinks_json,
        sources_json
    )
}

// C-ABI Exports
#[no_mangle]
pub unsafe extern "C" fn audio_query_all_json(
    card: *const c_char,
    out_buf: *mut c_char,
    max_len: usize,
) -> c_int {
    if out_buf.is_null() || max_len == 0 {
        return -1;
    }

    let card_str = if card.is_null() {
        "1"
    } else {
        CStr::from_ptr(card).to_str().unwrap_or("1")
    };

    let json = build_all_json(card_str);
    let c_str = match CString::new(json) {
        Ok(s) => s,
        Err(_) => return -2,
    };

    let bytes = c_str.as_bytes_with_nul();
    if bytes.len() > max_len {
        return -3; // Buffer too small
    }

    std::ptr::copy_nonoverlapping(bytes.as_ptr() as *const c_char, out_buf, bytes.len());
    0
}

#[no_mangle]
pub unsafe extern "C" fn audio_query_alsa_json(
    card: *const c_char,
    out_buf: *mut c_char,
    max_len: usize,
) -> c_int {
    if out_buf.is_null() || max_len == 0 {
        return -1;
    }

    let card_str = if card.is_null() {
        "1"
    } else {
        CStr::from_ptr(card).to_str().unwrap_or("1")
    };

    let json = build_alsa_json(card_str);
    let c_str = match CString::new(json) {
        Ok(s) => s,
        Err(_) => return -2,
    };

    let bytes = c_str.as_bytes_with_nul();
    if bytes.len() > max_len {
        return -3;
    }

    std::ptr::copy_nonoverlapping(bytes.as_ptr() as *const c_char, out_buf, bytes.len());
    0
}

#[no_mangle]
pub unsafe extern "C" fn audio_set_alsa_volume(
    card: *const c_char,
    name: *const c_char,
    percent: c_int,
) -> c_int {
    if name.is_null() {
        return -1;
    }

    let card_str = if card.is_null() { "1" } else { CStr::from_ptr(card).to_str().unwrap_or("1") };
    let name_str = match CStr::from_ptr(name).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };

    let mixer = match alsa::MixerHandle::open(card_str) {
        Ok(m) => m,
        Err(e) => return e,
    };

    match mixer.set_volume(name_str, percent) {
        Ok(_) => 0,
        Err(e) => e,
    }
}

#[no_mangle]
pub unsafe extern "C" fn audio_set_alsa_switch(
    card: *const c_char,
    name: *const c_char,
    unmute: c_int,
) -> c_int {
    if name.is_null() {
        return -1;
    }

    let card_str = if card.is_null() { "1" } else { CStr::from_ptr(card).to_str().unwrap_or("1") };
    let name_str = match CStr::from_ptr(name).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };

    let mixer = match alsa::MixerHandle::open(card_str) {
        Ok(m) => m,
        Err(e) => return e,
    };

    match mixer.set_switch(name_str, unmute != 0) {
        Ok(_) => 0,
        Err(e) => e,
    }
}

#[no_mangle]
pub unsafe extern "C" fn audio_set_alsa_auto_mute(
    card: *const c_char,
    enabled: c_int,
) -> c_int {
    let card_str = if card.is_null() { "1" } else { CStr::from_ptr(card).to_str().unwrap_or("1") };
    let mixer = match alsa::MixerHandle::open(card_str) {
        Ok(m) => m,
        Err(e) => return e,
    };

    match mixer.set_auto_mute_mode(enabled != 0) {
        Ok(_) => 0,
        Err(e) => e,
    }
}

#[no_mangle]
pub unsafe extern "C" fn audio_set_alsa_capture_source(
    card: *const c_char,
    source: *const c_char,
) -> c_int {
    if source.is_null() {
        return -1;
    }

    let card_str = if card.is_null() { "1" } else { CStr::from_ptr(card).to_str().unwrap_or("1") };
    let source_str = match CStr::from_ptr(source).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };

    let mixer = match alsa::MixerHandle::open(card_str) {
        Ok(m) => m,
        Err(e) => return e,
    };

    match mixer.set_capture_source(source_str) {
        Ok(_) => 0,
        Err(e) => e,
    }
}

#[no_mangle]
pub unsafe extern "C" fn audio_set_alsa_raw_volume(
    card: *const c_char,
    name: *const c_char,
    raw_val: c_int,
) -> c_int {
    if name.is_null() {
        return -1;
    }

    let card_str = if card.is_null() { "1" } else { CStr::from_ptr(card).to_str().unwrap_or("1") };
    let name_str = match CStr::from_ptr(name).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };

    let mixer = match alsa::MixerHandle::open(card_str) {
        Ok(m) => m,
        Err(e) => return e,
    };

    match mixer.set_raw_volume(name_str, raw_val) {
        Ok(_) => 0,
        Err(e) => e,
    }
}
