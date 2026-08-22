"""
DEPRECATED: This file is NO LONGER the recommended RF visualization approach.

The spectrum visualization functionality has been separated:
- Use rf/service.py for the headless RF acquisition service on Pi #2
- Use the laptop frontend for RF visualization via WebSocket streaming
- GUI components (matplotlib/TkAgg) are not suitable for headless Pi deployment

For RF visualization:
1. Run rf/service.py on Pi #2 for headless RF acquisition
2. The laptop frontend receives RF frames via /ws/rf WebSocket
3. Frontend components handle visualization with web technologies

This file is retained for reference but should not be used in production.
The matplotlib/TkAgg dependencies are not suitable for the target architecture.

Migration path:
- Use rf/service.py for Pi #2 RF service
- Frontend handles visualization via web technologies
- No GUI dependencies on RF acquisition node
"""

import logging
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s'
)
log = logging.getLogger('spectrum_display_deprecated')

def calculate_spectrum_fft(samples: np.ndarray, sample_rate: int, center_freq: float) -> tuple:
    """
    Calculate power spectrum from IQ samples (headless version).
    
    This function extracts the DSP logic from the original spectrum_display.py
    for use in headless environments without GUI dependencies.
    
    Args:
        samples: Complex IQ samples
        sample_rate: Sample rate in Hz
        center_freq: Center frequency in Hz
        
    Returns:
        Tuple of (frequencies_mhz, power_dbm)
    """
    fft_size = len(samples)
    window = np.hanning(fft_size)
    fft_out = np.fft.fftshift(np.fft.fft(samples * window))
    power_db = 20 * np.log10(np.abs(fft_out) + 1e-12)
    
    freqs = np.fft.fftshift(np.fft.fftfreq(fft_size, d=1.0 / sample_rate))
    freqs_mhz = (center_freq / 1e6) + (freqs / 1e6)
    
    return freqs_mhz, power_db


def calculate_snr(samples: np.ndarray) -> float:
    """
    Calculate SNR from IQ samples (headless version).
    
    Args:
        samples: Complex IQ samples
        
    Returns:
        SNR in dB
    """
    power = np.abs(samples) ** 2
    n = len(power)
    center = power[int(n*0.45):int(n*0.55)]
    noise = np.concatenate([power[:int(n*0.45)], power[int(n*0.55):]])
    sig_pwr = np.mean(center)
    noise_pwr = np.mean(noise)
    
    if noise_pwr == 0:
        return 0.0
    
    snr = 10 * np.log10(sig_pwr / noise_pwr)
    return round(float(snr), 2)


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  DEPRECATED: spectrum_display.py")
    print("=" * 60)
    print("\nThis file is deprecated and should not be used in production.")
    print("\nFor RF visualization:")
    print("  1. Use rf/service.py for headless RF acquisition on Pi #2")
    print("  2. Use laptop frontend for visualization via WebSocket")
    print("  3. No GUI dependencies on RF acquisition node")
    print("\nDSP functions are available for headless use:")
    print("  - calculate_spectrum_fft()")
    print("  - calculate_snr()")
    print("=" * 60 + "\n")
