use std::ffi::{CStr, CString};
use std::os::raw::{c_char, c_int, c_long, c_uint, c_void};
use std::ptr;

// Opaque ALSA types
type SndMixerT = c_void;
type SndMixerElemT = c_void;
type SndMixerSelemIdT = c_void;

extern "C" {
    fn snd_mixer_open(mixerp: *mut *mut SndMixerT, mode: c_int) -> c_int;
    fn snd_mixer_close(mixer: *mut SndMixerT) -> c_int;
    fn snd_mixer_attach(mixer: *mut SndMixerT, name: *const c_char) -> c_int;
    fn snd_mixer_selem_register(
        mixer: *mut SndMixerT,
        options: *mut c_void,
        classp: *mut *mut c_void,
    ) -> c_int;
    fn snd_mixer_load(mixer: *mut SndMixerT) -> c_int;

    fn snd_mixer_selem_id_sizeof() -> usize;
    fn snd_mixer_selem_id_set_name(id: *mut SndMixerSelemIdT, name: *const c_char);
    fn snd_mixer_selem_id_set_index(id: *mut SndMixerSelemIdT, index: c_uint);
    fn snd_mixer_find_selem(mixer: *mut SndMixerT, id: *const SndMixerSelemIdT) -> *mut SndMixerElemT;

    fn snd_mixer_selem_has_playback_volume(elem: *mut SndMixerElemT) -> c_int;
    fn snd_mixer_selem_get_playback_volume_range(
        elem: *mut SndMixerElemT,
        min: *mut c_long,
        max: *mut c_long,
    ) -> c_int;
    fn snd_mixer_selem_get_playback_volume(
        elem: *mut SndMixerElemT,
        channel: c_int,
        value: *mut c_long,
    ) -> c_int;
    fn snd_mixer_selem_set_playback_volume_all(elem: *mut SndMixerElemT, value: c_long) -> c_int;

    fn snd_mixer_selem_has_playback_switch(elem: *mut SndMixerElemT) -> c_int;
    fn snd_mixer_selem_get_playback_switch(
        elem: *mut SndMixerElemT,
        channel: c_int,
        value: *mut c_int,
    ) -> c_int;
    fn snd_mixer_selem_set_playback_switch_all(elem: *mut SndMixerElemT, value: c_int) -> c_int;

    fn snd_mixer_selem_has_capture_volume(elem: *mut SndMixerElemT) -> c_int;
    fn snd_mixer_selem_get_capture_volume_range(
        elem: *mut SndMixerElemT,
        min: *mut c_long,
        max: *mut c_long,
    ) -> c_int;
    fn snd_mixer_selem_get_capture_volume(
        elem: *mut SndMixerElemT,
        channel: c_int,
        value: *mut c_long,
    ) -> c_int;
    fn snd_mixer_selem_set_capture_volume_all(elem: *mut SndMixerElemT, value: c_long) -> c_int;

    fn snd_mixer_selem_has_capture_switch(elem: *mut SndMixerElemT) -> c_int;
    fn snd_mixer_selem_get_capture_switch(
        elem: *mut SndMixerElemT,
        channel: c_int,
        value: *mut c_int,
    ) -> c_int;
    fn snd_mixer_selem_set_capture_switch_all(elem: *mut SndMixerElemT, value: c_int) -> c_int;

    fn snd_mixer_selem_is_enumerated(elem: *mut SndMixerElemT) -> c_int;
    fn snd_mixer_selem_get_enum_items(elem: *mut SndMixerElemT) -> c_int;
    fn snd_mixer_selem_get_enum_item(
        elem: *mut SndMixerElemT,
        channel: c_int,
        itemp: *mut c_uint,
    ) -> c_int;
    fn snd_mixer_selem_get_enum_item_name(
        elem: *mut SndMixerElemT,
        item: c_uint,
        max_len: usize,
        str: *mut c_char,
    ) -> c_int;
    fn snd_mixer_selem_set_enum_item(
        elem: *mut SndMixerElemT,
        channel: c_int,
        item: c_uint,
    ) -> c_int;
}

pub struct MixerHandle {
    handle: *mut SndMixerT,
}

impl MixerHandle {
    pub fn open(card: &str) -> Result<Self, i32> {
        let card_name = if card.starts_with("hw:") {
            CString::new(card).unwrap()
        } else {
            CString::new(format!("hw:{}", card)).unwrap()
        };

        unsafe {
            let mut handle: *mut SndMixerT = ptr::null_mut();
            let err = snd_mixer_open(&mut handle, 0);
            if err < 0 || handle.is_null() {
                return Err(err);
            }

            let err = snd_mixer_attach(handle, card_name.as_ptr());
            if err < 0 {
                snd_mixer_close(handle);
                return Err(err);
            }

            let err = snd_mixer_selem_register(handle, ptr::null_mut(), ptr::null_mut());
            if err < 0 {
                snd_mixer_close(handle);
                return Err(err);
            }

            let err = snd_mixer_load(handle);
            if err < 0 {
                snd_mixer_close(handle);
                return Err(err);
            }

            Ok(MixerHandle { handle })
        }
    }

    fn find_elem(&self, name: &str) -> *mut SndMixerElemT {
        unsafe {
            let size = snd_mixer_selem_id_sizeof();
            let mut buf = vec![0u8; size];
            let id = buf.as_mut_ptr() as *mut SndMixerSelemIdT;

            let c_name = CString::new(name).unwrap();
            snd_mixer_selem_id_set_name(id, c_name.as_ptr());
            snd_mixer_selem_id_set_index(id, 0);

            snd_mixer_find_selem(self.handle, id)
        }
    }

    pub fn get_volume_and_switch(&self, name: &str) -> Option<(i32, bool)> {
        let elem = self.find_elem(name);
        if elem.is_null() {
            return None;
        }

        unsafe {
            let has_play_vol = snd_mixer_selem_has_playback_volume(elem) != 0;
            let has_cap_vol = snd_mixer_selem_has_capture_volume(elem) != 0;

            let (vol_pct, has_vol) = if has_play_vol {
                let mut min: c_long = 0;
                let mut max: c_long = 100;
                snd_mixer_selem_get_playback_volume_range(elem, &mut min, &mut max);

                let mut val: c_long = 0;
                snd_mixer_selem_get_playback_volume(elem, 0, &mut val);

                let pct = if max > min {
                    let range = max - min;
                    ((val - min) * 100 + range / 2) / range
                } else {
                    100
                };
                (pct as i32, true)
            } else if has_cap_vol {
                let mut min: c_long = 0;
                let mut max: c_long = 100;
                snd_mixer_selem_get_capture_volume_range(elem, &mut min, &mut max);

                let mut val: c_long = 0;
                snd_mixer_selem_get_capture_volume(elem, 0, &mut val);

                let pct = if max > min {
                    let range = max - min;
                    ((val - min) * 100 + range / 2) / range
                } else {
                    100
                };
                (pct as i32, true)
            } else {
                (100, false)
            };

            let mut sw_val: c_int = 1;
            if snd_mixer_selem_has_playback_switch(elem) != 0 {
                snd_mixer_selem_get_playback_switch(elem, 0, &mut sw_val);
            } else if snd_mixer_selem_has_capture_switch(elem) != 0 {
                snd_mixer_selem_get_capture_switch(elem, 0, &mut sw_val);
            }

            if has_vol || snd_mixer_selem_has_playback_switch(elem) != 0 || snd_mixer_selem_has_capture_switch(elem) != 0 {
                Some((vol_pct, sw_val != 0))
            } else {
                None
            }
        }
    }

    pub fn set_volume(&self, name: &str, percent: i32) -> Result<(), i32> {
        let elem = self.find_elem(name);
        if elem.is_null() {
            return Err(-1);
        }

        let clamped = percent.clamp(0, 100);

        unsafe {
            if snd_mixer_selem_has_playback_volume(elem) != 0 {
                let mut min: c_long = 0;
                let mut max: c_long = 100;
                snd_mixer_selem_get_playback_volume_range(elem, &mut min, &mut max);
                let raw_val = min + (clamped as c_long * (max - min) + 50) / 100;
                let res = snd_mixer_selem_set_playback_volume_all(elem, raw_val);
                if res < 0 {
                    return Err(res);
                }
                Ok(())
            } else if snd_mixer_selem_has_capture_volume(elem) != 0 {
                let mut min: c_long = 0;
                let mut max: c_long = 100;
                snd_mixer_selem_get_capture_volume_range(elem, &mut min, &mut max);
                let raw_val = min + (clamped as c_long * (max - min) + 50) / 100;
                let res = snd_mixer_selem_set_capture_volume_all(elem, raw_val);
                if res < 0 {
                    return Err(res);
                }
                Ok(())
            } else {
                Err(-2)
            }
        }
    }

    pub fn set_switch(&self, name: &str, unmute: bool) -> Result<(), i32> {
        let elem = self.find_elem(name);
        if elem.is_null() {
            return Err(-1);
        }

        let val: c_int = if unmute { 1 } else { 0 };

        unsafe {
            let mut applied = false;
            if snd_mixer_selem_has_playback_switch(elem) != 0 {
                let res = snd_mixer_selem_set_playback_switch_all(elem, val);
                if res < 0 {
                    return Err(res);
                }
                applied = true;
            }
            if snd_mixer_selem_has_capture_switch(elem) != 0 {
                let res = snd_mixer_selem_set_capture_switch_all(elem, val);
                if res < 0 {
                    return Err(res);
                }
                applied = true;
            }

            if applied {
                Ok(())
            } else {
                Err(-2)
            }
        }
    }

    pub fn get_auto_mute_mode(&self) -> Option<String> {
        let elem = self.find_elem("Auto-Mute Mode");
        if elem.is_null() {
            return None;
        }

        unsafe {
            if snd_mixer_selem_is_enumerated(elem) == 0 {
                return None;
            }

            let mut item: c_uint = 0;
            if snd_mixer_selem_get_enum_item(elem, 0, &mut item) < 0 {
                return None;
            }

            let mut name_buf = [0 as c_char; 64];
            if snd_mixer_selem_get_enum_item_name(elem, item, 64, name_buf.as_mut_ptr()) < 0 {
                return None;
            }

            let c_str = CStr::from_ptr(name_buf.as_ptr());
            Some(c_str.to_string_lossy().into_owned())
        }
    }

    pub fn set_auto_mute_mode(&self, enable: bool) -> Result<(), i32> {
        let elem = self.find_elem("Auto-Mute Mode");
        if elem.is_null() {
            return Err(-1);
        }

        unsafe {
            let num_items = snd_mixer_selem_get_enum_items(elem);
            if num_items <= 0 {
                return Err(-2);
            }

            let target = if enable { "Enabled" } else { "Disabled" };

            for i in 0..(num_items as c_uint) {
                let mut name_buf = [0 as c_char; 64];
                if snd_mixer_selem_get_enum_item_name(elem, i, 64, name_buf.as_mut_ptr()) >= 0 {
                    let c_str = CStr::from_ptr(name_buf.as_ptr());
                    if c_str.to_string_lossy() == target {
                        let res = snd_mixer_selem_set_enum_item(elem, 0, i);
                        return if res >= 0 { Ok(()) } else { Err(res) };
                    }
                }
            }

            Err(-3)
        }
    }
}

impl Drop for MixerHandle {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            unsafe {
                snd_mixer_close(self.handle);
            }
        }
    }
}
