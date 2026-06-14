import matplotlib
matplotlib.use('TkAgg')
import logging
import threading
import time
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import uvicorn
from fastapi import FastAPI

from rtlsdr_reader import RTLSDRReader, MockRTLSDRReader
from meteor_predictor import MeteorPredictor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s'
)
log = logging.getLogger('spectrum_display')

def green(msg):  print(f"\033[92m{msg}\033[0m")
def yellow(msg): print(f"\033[93m{msg}\033[0m")
def red(msg):    print(f"\033[91m{msg}\033[0m")

SATELLITE     = "Meteor-M2-4"
NORAD         = 59051
FREQUENCY_MHZ = 137.9
PORT          = 8002
FREQUENCY_HZ  = FREQUENCY_MHZ * 1e6

rf_status = {
    "snr_db":                0.0,
    "pass_quality":          "WAITING",
    "satellite":             SATELLITE,
    "norad":                 NORAD,
    "next_pass_eta_min":     0.0,
    "frequency_mhz":         FREQUENCY_MHZ,
    "receiving":             False,
    "elevation_deg":         0.0,
    "doppler_correction_hz": 0.0,
}
rf_status_lock = threading.Lock()

# --- FastAPI ---
app = FastAPI()

@app.get('/rf/status')
def get_rf_status():
    with rf_status_lock:
        return dict(rf_status)

def start_api():
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='warning')

# --- Plot setup ---
fig, ax = plt.subplots(figsize=(12, 5))
fig.patch.set_facecolor('#0a0a0a')
ax.set_facecolor('#0d0d0d')

line_spectrum, = ax.plot([], [], color='#00ff41', linewidth=0.8)
vline = ax.axvline(x=FREQUENCY_MHZ, color='#00ff41', linewidth=1.5, linestyle='--', label='137.9 MHz')

ax.set_xlim(FREQUENCY_MHZ - 1.2, FREQUENCY_MHZ + 1.2)
ax.set_ylim(-110, -40)
ax.set_xlabel('Frequency (MHz)', color='#aaaaaa')
ax.set_ylabel('Power (dBm)',     color='#aaaaaa')
ax.tick_params(colors='#aaaaaa')
ax.grid(True, color='#1a1a1a', linewidth=0.5)
ax.legend(loc='upper right', facecolor='#1a1a1a', edgecolor='#333333',
          labelcolor='#aaaaaa', fontsize=9)
for spine in ax.spines.values():
    spine.set_edgecolor('#333333')

_reader    = None
_predictor = None


def update(frame):
    global rf_status

    try:
        samples         = _reader.read_samples()
        snr             = _reader.compute_snr(samples)
        velocity        = _predictor.get_range_velocity()
        tuned_freq_hz   = _reader.apply_doppler_correction(velocity)   # absolute freq in Hz
        doppler_shift   = round(tuned_freq_hz - FREQUENCY_HZ, 1)       # actual shift in Hz
        position        = _predictor.get_current_position()
        quality         = _predictor.get_pass_quality(position['elevation_deg'])
        eta             = _predictor.next_pass_eta_minutes()
        receiving       = snr > 5.0

        # FFT
        fft_size    = len(samples)
        window      = np.hanning(fft_size)
        fft_out     = np.fft.fftshift(np.fft.fft(samples * window))
        power_db    = 20 * np.log10(np.abs(fft_out) + 1e-12)
        sample_rate = getattr(_reader, 'sample_rate', 2_048_000)
        freqs       = np.fft.fftshift(np.fft.fftfreq(fft_size, d=1.0 / sample_rate))
        freqs_mhz   = FREQUENCY_MHZ + freqs / 1e6

        # Update plot
        line_spectrum.set_data(freqs_mhz, power_db)

        # Background color
        if receiving:
            fig.patch.set_facecolor('#001a00')
            ax.set_facecolor('#001a00')
        else:
            fig.patch.set_facecolor('#1a1700')
            ax.set_facecolor('#1a1700')

        # Title
        eta_str = f"{int(eta)}min" if eta < float('inf') else "N/A"
        ax.set_title(
            f"{SATELLITE} | {FREQUENCY_MHZ} MHz | SNR: {snr:.1f} dB | "
            f"Doppler: {doppler_shift:+.0f} Hz | {quality} | Next: {eta_str}",
            color='#00ff41', fontsize=11, fontweight='bold'
        )

        # Update rf_status
        with rf_status_lock:
            rf_status['snr_db']                = round(snr, 1)
            rf_status['pass_quality']          = quality if receiving else 'WAITING'
            rf_status['next_pass_eta_min']     = round(eta, 1) if eta < float('inf') else 0.0
            rf_status['receiving']             = receiving
            rf_status['elevation_deg']         = position['elevation_deg']
            rf_status['doppler_correction_hz'] = doppler_shift

        if receiving:
            green(f"[SPECTRUM] RECEIVING — SNR={snr:.1f}dB tuned={tuned_freq_hz/1e6:.4f}MHz shift={doppler_shift:+.1f}Hz el={position['elevation_deg']}°")
        else:
            log.info(f"Waiting — SNR={snr:.1f}dB eta={eta_str} el={position['elevation_deg']}°")

    except Exception as e:
        log.warning(f"Update error: {e}")

    return line_spectrum, vline


if __name__ == '__main__':
    print("\n" + "=" * 60)
    green("  DeadSat Ground Station — Spectrum Display")
    green(f"  {SATELLITE} | {FREQUENCY_MHZ} MHz | Port {PORT}")
    print("=" * 60 + "\n")

    # Start FastAPI thread
    api_thread = threading.Thread(target=start_api, daemon=True)
    api_thread.start()
    green(f"[API] RF status server started — http://0.0.0.0:{PORT}/rf/status")

    # Init predictor
    _predictor = MeteorPredictor()
    p = _predictor.get_next_pass()
    if p['aos']:
        q  = p['pass_quality']
        fn = green if q in ('EXCELLENT', 'GOOD') else (yellow if q == 'WEAK' else red)
        fn(f"[PASS] Next pass — AOS: {p['aos']} | Max el: {p['max_elevation_deg']}° | Quality: {q}")
    else:
        red("[PASS] No pass found in next 24h")

    # Init reader — auto mock if no device
    try:
        _reader = RTLSDRReader()
        green("[SDR] RTL-SDR device opened ✅")
    except Exception as e:
        yellow(f"[SDR] No RTL-SDR device — using mock ({e})")
        _reader = MockRTLSDRReader()
        yellow("[SDR] MockRTLSDRReader active")

    # Start animation
    ani = animation.FuncAnimation(
        fig, update,
        interval=500,
        blit=True,
        cache_frame_data=False
    )

    try:
        plt.show()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            _reader.close()
            green("[SDR] Reader closed")
        except Exception:
            pass
        green("[SPECTRUM] Display closed")
        print("=" * 60 + "\n")
