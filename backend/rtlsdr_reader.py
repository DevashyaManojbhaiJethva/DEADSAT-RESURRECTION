"""
DeadSat Resurrection — RTL-SDR Reader
Pi #2 module — Meteor-M2-3/4 LRPT Reception

Receives live LRPT signal from Meteor-M2-3 or Meteor-M2-4
on 137.900 MHz using RTL-SDR dongle.

Note: NOAA-18 was decommissioned June 2025.
      Meteor-M2-3/4 are the active 137 MHz targets in 2026.

Hardware:
  - RTL-SDR Blog V3 dongle
  - Simple wire dipole antenna (53.4cm each arm for 137 MHz)
  - Raspberry Pi 4 #2

Dependencies:
  pip install pyrtlsdr numpy
  sudo apt-get install rtl-sdr

Usage:
  python3 rtlsdr_reader.py
"""

import time
import json
import threading
from datetime import datetime, timezone
from typing import Optional

# RTL-SDR config
FREQUENCY_HZ     = 137_900_000    # Meteor-M2-3/4 LRPT frequency (137.900 MHz)
FREQUENCY_MHZ    = 137.900
SAMPLE_RATE      = 2_400_000      # 2.4 MSPS
GAIN             = 49.6           # dB — max gain for weak satellite signals
PPM_CORRECTION   = 0              # frequency correction (calibrate with known signal)
BUFFER_SIZE      = 1024 * 16

# Target satellites
METEOR_M2_3_NORAD = 57166
METEOR_M2_4_NORAD = 59051
PRIMARY_NORAD     = METEOR_M2_3_NORAD

# Backend endpoint (Pi #1)
BACKEND_URL = "http://10.36.220.90:8000"   # Update if Pi #1 IP changes


class RTLSDRReader:
    """
    Reads live RF signal from Meteor-M2-3/4 on 137.900 MHz.
    Streams signal strength and basic LRPT frame info to Pi #1 backend.
    """

    def __init__(self):
        self.sdr        = None
        self.running    = False
        self.signal_dbm = -100.0
        self.frame_count = 0
        self._lock      = threading.Lock()

    def start(self):
        """Initialize RTL-SDR and start sampling."""
        try:
            from rtlsdr import RtlSdr  # type: ignore
            self.sdr = RtlSdr()  # type: ignore
            self.sdr.sample_rate    = SAMPLE_RATE
            self.sdr.center_freq    = FREQUENCY_HZ
            self.sdr.gain           = GAIN
            self.sdr.freq_correction = PPM_CORRECTION

            self.running = True
            t = threading.Thread(target=self._sample_loop, daemon=True)
            t.start()
            print(f"[RTL-SDR] Started — {FREQUENCY_MHZ} MHz (Meteor-M2-3/4)")
            print(f"[RTL-SDR] Sample rate: {SAMPLE_RATE/1e6} MSPS | Gain: {GAIN} dB")
        except ImportError:
            print("[RTL-SDR] pyrtlsdr not installed — running in MOCK mode")
            self.running = True
            t = threading.Thread(target=self._mock_loop, daemon=True)
            t.start()
        except Exception as e:
            print(f"[RTL-SDR] Hardware error: {e} — running in MOCK mode")
            self.running = True
            t = threading.Thread(target=self._mock_loop, daemon=True)
            t.start()

    def stop(self):
        self.running = False
        if self.sdr:
            try:
                self.sdr.close()
            except Exception:
                pass
        print("[RTL-SDR] Stopped")

    def _sample_loop(self):
        """Real RTL-SDR sampling loop."""
        import numpy as np
        while self.running:
            try:
                samples = self.sdr.read_samples(BUFFER_SIZE)  # type: ignore
                power   = np.mean(np.abs(samples) ** 2)
                dbm     = 10 * np.log10(power + 1e-12) + 30
                with self._lock:
                    self.signal_dbm  = round(float(dbm), 2)
                    self.frame_count += 1
                self._push_to_backend()
                time.sleep(1.0)
            except Exception as e:
                print(f"[RTL-SDR] Sample error: {e}")
                time.sleep(2.0)

    def _mock_loop(self):
        """Mock loop for development without hardware."""
        import math, random
        t = 0
        while self.running:
            # Simulate realistic signal fluctuation
            base     = -82.0
            noise    = random.gauss(0, 2.5)
            # Simulate pass: signal strengthens every ~6000s
            pass_sim = 8 * math.sin(t / 300) if (t % 600) < 300 else 0
            with self._lock:
                self.signal_dbm  = round(base + noise + pass_sim, 2)
                self.frame_count += 1
            self._push_to_backend()
            t    += 1
            time.sleep(1.0)

    def _push_to_backend(self):
        """Push signal reading to Pi #1 backend (non-blocking)."""
        try:
            import urllib.request
            payload = json.dumps({
                "timestamp":     int(time.time()),
                "frequency_mhz": FREQUENCY_MHZ,
                "signal_dbm":    self.signal_dbm,
                "norad_id":      PRIMARY_NORAD,
                "satellite":     "METEOR-M2-3",
                "frame_count":   self.frame_count,
            }).encode()
            req = urllib.request.Request(
                f"{BACKEND_URL}/rf/signal",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=1)
        except Exception:
            pass  # Non-blocking — backend may not have /rf/signal endpoint yet

    def get_status(self) -> dict:
        with self._lock:
            return {
                "frequency_mhz":  FREQUENCY_MHZ,
                "signal_dbm":     self.signal_dbm,
                "norad_id":       PRIMARY_NORAD,
                "satellite":      "METEOR-M2-3",
                "frame_count":    self.frame_count,
                "timestamp":      datetime.now(timezone.utc).isoformat(),
            }


# ──────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("DeadSat RTL-SDR Reader — Pi #2")
    print(f"Target: Meteor-M2-3 (NORAD {METEOR_M2_3_NORAD})")
    print(f"Frequency: {FREQUENCY_MHZ} MHz LRPT")
    print(f"Note: NOAA-18 decommissioned June 2025")
    print("=" * 50)

    reader = RTLSDRReader()
    reader.start()

    try:
        while True:
            time.sleep(5)
            status = reader.get_status()
            print(f"[RTL-SDR] Signal: {status['signal_dbm']} dBm | "
                  f"Frames: {status['frame_count']} | "
                  f"Sat: {status['satellite']}")
    except KeyboardInterrupt:
        reader.stop()
        print("\n[RTL-SDR] Shut down cleanly")