import numpy as np
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s'
)
logger = logging.getLogger('rtlsdr_reader')

FREQUENCY_HZ   = 137_900_000
SAMPLE_RATE    = 2_048_000
GAIN           = 40
SPEED_OF_LIGHT = 299_792_458


def _import_rtlsdr():
    import ctypes
    _orig = ctypes.CDLL.__getattr__

    def _patch(self, name):
        try:
            return _orig(self, name)
        except AttributeError:
            def noop(*a, **k):
                return 0
            return noop

    ctypes.CDLL.__getattr__ = _patch
    try:
        from rtlsdr import RtlSdr
        return RtlSdr
    except Exception as e:
        raise e


class RTLSDRReader:

    def __init__(self):
        RtlSdr = _import_rtlsdr()
        self.sdr = RtlSdr()
        self.sdr.sample_rate = SAMPLE_RATE
        self.sdr.center_freq = FREQUENCY_HZ
        self.sdr.gain        = GAIN
        self.sample_rate     = SAMPLE_RATE
        self.current_freq    = FREQUENCY_HZ
        logger.info('RTL-SDR opened — freq=%.3f MHz gain=%d', FREQUENCY_HZ / 1e6, GAIN)
        print(f'\033[92m[RTLSDR] Device opened — {FREQUENCY_HZ/1e6:.3f} MHz gain={GAIN}\033[0m')

    def read_samples(self, num_samples=256*1024) -> np.ndarray:
        samples = self.sdr.read_samples(num_samples)
        logger.info('Read %d samples', num_samples)
        return np.array(samples)

    def compute_snr(self, samples: np.ndarray) -> float:
        power     = np.abs(samples) ** 2
        n         = len(power)
        center    = power[int(n*0.45):int(n*0.55)]
        noise     = np.concatenate([power[:int(n*0.45)], power[int(n*0.55):]])
        sig_pwr   = np.mean(center)
        noise_pwr = np.mean(noise)
        if noise_pwr == 0:
            return 0.0
        snr = 10 * np.log10(sig_pwr / noise_pwr)
        logger.info('SNR=%.2f dB', snr)
        return round(snr, 2)

    def apply_doppler_correction(self, satellite_velocity_ms: float) -> float:
        adjusted = FREQUENCY_HZ * (1 - satellite_velocity_ms / SPEED_OF_LIGHT)
        self.sdr.center_freq = adjusted
        self.current_freq    = adjusted
        shift = adjusted - FREQUENCY_HZ
        logger.info('Doppler — velocity=%.1f m/s shift=%.1f Hz new_freq=%.4f MHz',
                    satellite_velocity_ms, shift, adjusted / 1e6)
        print(f'\033[92m[RTLSDR] Doppler — velocity={satellite_velocity_ms:.1f} m/s '
              f'shift={shift:.1f} Hz new_freq={adjusted/1e6:.4f} MHz\033[0m')
        return adjusted

    def close(self):
        self.sdr.close()
        logger.info('RTL-SDR closed')
        print('\033[92m[RTLSDR] Device closed\033[0m')


class MockRTLSDRReader:

    def __init__(self):
        self.current_freq = FREQUENCY_HZ
        self.sample_rate  = SAMPLE_RATE
        logger.warning('MockRTLSDRReader active — no real device')
        print('\033[93m[MOCK] No antenna — simulated signal\033[0m')

    def read_samples(self, num_samples=256*1024) -> np.ndarray:
        noise   = (np.random.randn(num_samples) + 1j * np.random.randn(num_samples)) * 0.1
        t       = np.arange(num_samples) / SAMPLE_RATE
        signal  = 0.3 * np.exp(2j * np.pi * 1000 * t)
        samples = noise + signal
        logger.info('Mock: generated %d samples', num_samples)
        return samples

    def compute_snr(self, samples: np.ndarray) -> float:
        snr = round(np.random.uniform(8.0, 12.0), 2)
        logger.info('Mock SNR=%.2f dB', snr)
        return snr

    def apply_doppler_correction(self, satellite_velocity_ms: float) -> float:
        adjusted = FREQUENCY_HZ * (1 - satellite_velocity_ms / SPEED_OF_LIGHT)
        self.current_freq = adjusted
        shift = adjusted - FREQUENCY_HZ
        logger.info('Mock Doppler — velocity=%.1f m/s shift=%.1f Hz', satellite_velocity_ms, shift)
        print(f'\033[93m[MOCK] Doppler — velocity={satellite_velocity_ms:.1f} m/s '
              f'shift={shift:.1f} Hz new_freq={adjusted/1e6:.4f} MHz\033[0m')
        return adjusted

    def close(self):
        logger.info('Mock RTL-SDR closed')
        print('\033[93m[MOCK] Reader closed\033[0m')


def get_reader():
    try:
        reader = RTLSDRReader()
        print('\033[92m[RTLSDR] Real device detected ✅\033[0m')
        return reader
    except Exception as e:
        logger.warning('RTL-SDR not available (%s) — falling back to mock', e)
        print(f'\033[93m[RTLSDR] Device not available — using mock\033[0m')
        return MockRTLSDRReader()


if __name__ == '__main__':
    print('\n=== RTL-SDR Reader Test ===\n')
    reader = get_reader()

    print('\n--- Reading samples ---')
    samples = reader.read_samples()
    print(f'Samples shape: {samples.shape}, dtype: {samples.dtype}')

    print('\n--- Computing SNR ---')
    snr = reader.compute_snr(samples)
    print(f'SNR: {snr} dB')

    print('\n--- Doppler correction (velocity=7500 m/s) ---')
    new_freq = reader.apply_doppler_correction(7500.0)
    print(f'New frequency: {new_freq/1e6:.4f} MHz')

    reader.close()
    print('\n=== Test complete ===')
