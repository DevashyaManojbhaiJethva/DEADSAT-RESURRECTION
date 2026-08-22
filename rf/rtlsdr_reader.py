"""
DeadSat Resurrection — Authoritative RTL-SDR Reader
==================================================

This is the canonical RTL-SDR acquisition implementation for Pi #2.
It provides a clean, reusable interface that produces structured RFFrame objects.

Hardware:
  - RTL-SDR Blog V3 dongle
  - Simple wire dipole antenna (53.4cm each arm for 137 MHz)
  - Raspberry Pi 4 #2

Dependencies:
  pip install pyrtlsdr numpy
  sudo apt-get install rtl-sdr

Usage:
    from rf.rtlsdr_reader import RTLSDRReader, get_reader
    from rf.models import RFFrame
    
    reader = get_reader()
    frame = reader.read_frame()
"""

import numpy as np
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from rf.models import RFFrame, RFNodeType, RFHealthStatus, RFMode

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s'
)
logger = logging.getLogger('rtlsdr_reader')

# Default RF parameters
DEFAULT_FREQUENCY_HZ   = 137_900_000  # Meteor-M2-3/4 LRPT frequency
DEFAULT_SAMPLE_RATE    = 2_400_000    # 2.4 MSPS
DEFAULT_GAIN           = 49.6         # dB — max gain for weak satellite signals
DEFAULT_PPM_CORRECTION = 0            # frequency correction
DEFAULT_BUFFER_SIZE    = 1024 * 16
SPEED_OF_LIGHT         = 299_792_458


def _import_rtlsdr():
    """
    Import RTL-SDR library with error handling for missing hardware.
    
    Uses a patch to prevent crashes when the RTL-SDR library is unavailable
    or when the device is not connected.
    """
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
    """
    Authoritative RTL-SDR reader implementation.
    
    Produces structured RFFrame objects with comprehensive RF telemetry.
    Handles device initialization, error recovery, and graceful degradation.
    """

    def __init__(self, 
                 frequency_hz: float = DEFAULT_FREQUENCY_HZ,
                 sample_rate: int = DEFAULT_SAMPLE_RATE,
                 gain: float = DEFAULT_GAIN,
                 ppm_correction: int = DEFAULT_PPM_CORRECTION):
        """
        Initialize RTL-SDR reader with configurable parameters.
        
        Args:
            frequency_hz: Center frequency in Hz
            sample_rate: Sample rate in samples per second
            gain: RF gain in dB
            ppm_correction: PPM correction for frequency accuracy
        """
        try:
            RtlSdr = _import_rtlsdr()
            self.sdr = RtlSdr()
            self.sdr.sample_rate = sample_rate
            self.sdr.center_freq = frequency_hz
            self.sdr.gain = gain
            self.sdr.freq_correction = ppm_correction
            
            self.frequency_hz = frequency_hz
            self.sample_rate = sample_rate
            self.gain = gain
            self.current_freq = frequency_hz
            self.ppm_correction = ppm_correction
            
            self._sequence = 0
            self._lock = threading.Lock()
            self._running = False
            
            logger.info('RTL-SDR opened — freq=%.3f MHz gain=%.1f dB sample_rate=%.1f MSPS', 
                       frequency_hz / 1e6, gain, sample_rate / 1e6)
            print(f'\033[92m[RTLSDR] Device opened — {frequency_hz/1e6:.3f} MHz gain={gain:.1f} dB\033[0m')
            
        except Exception as e:
            logger.error('RTL-SDR initialization failed: %s', e)
            raise RuntimeError(f"RTL-SDR initialization failed: {e}")

    def read_samples(self, num_samples: int = DEFAULT_BUFFER_SIZE) -> np.ndarray:
        """
        Read samples from RTL-SDR device.
        
        Args:
            num_samples: Number of samples to read
            
        Returns:
            Complex numpy array of IQ samples
        """
        try:
            samples = self.sdr.read_samples(num_samples)
            logger.debug('Read %d samples', num_samples)
            return np.array(samples)
        except Exception as e:
            logger.error('Sample read failed: %s', e)
            raise RuntimeError(f"Sample read failed: {e}")

    def compute_snr(self, samples: np.ndarray) -> float:
        """
        Compute signal-to-noise ratio from IQ samples.
        
        Uses center 10% of spectrum as signal, edges as noise.
        
        Args:
            samples: Complex IQ samples
            
        Returns:
            SNR in dB
        """
        power = np.abs(samples) ** 2
        n = len(power)
        
        # Use center 10% as signal, rest as noise
        center_start = int(n * 0.45)
        center_end = int(n * 0.55)
        center = power[center_start:center_end]
        noise = np.concatenate([power[:center_start], power[center_end:]])
        
        sig_pwr = np.mean(center)
        noise_pwr = np.mean(noise)
        
        if noise_pwr == 0:
            return 0.0
        
        snr = 10 * np.log10(sig_pwr / noise_pwr)
        logger.debug('SNR=%.2f dB', snr)
        return round(float(snr), 2)

    def compute_signal_dbm(self, samples: np.ndarray) -> float:
        """
        Compute signal strength in dBm from IQ samples.
        
        Args:
            samples: Complex IQ samples
            
        Returns:
            Signal strength in dBm
        """
        power = np.mean(np.abs(samples) ** 2)
        dbm = 10 * np.log10(power + 1e-12) + 30
        logger.debug('Signal=%.2f dBm', dbm)
        return round(float(dbm), 2)

    def compute_spectrum(self, samples: np.ndarray, fft_size: Optional[int] = None) -> tuple:
        """
        Compute power spectrum from IQ samples.
        
        Args:
            samples: Complex IQ samples
            fft_size: FFT size (defaults to sample length)
            
        Returns:
            Tuple of (frequencies_hz, power_dbm)
        """
        if fft_size is None:
            fft_size = len(samples)
        
        # Apply window to reduce spectral leakage
        window = np.hanning(fft_size)
        if len(samples) >= fft_size:
            samples_windowed = samples[:fft_size] * window
        else:
            # Zero-pad if samples are shorter than FFT size
            padded = np.zeros(fft_size, dtype=complex)
            padded[:len(samples)] = samples
            samples_windowed = padded * window
        
        # Compute FFT and shift
        fft_out = np.fft.fftshift(np.fft.fft(samples_windowed))
        power_db = 20 * np.log10(np.abs(fft_out) + 1e-12)
        
        # Compute frequency axis
        freqs = np.fft.fftshift(np.fft.fftfreq(fft_size, d=1.0 / self.sample_rate))
        freqs_hz = self.current_freq + freqs
        
        return freqs_hz.tolist(), power_db.tolist()

    def apply_doppler_correction(self, satellite_velocity_ms: float) -> float:
        """
        Apply Doppler correction based on satellite range rate.
        
        Args:
            satellite_velocity_ms: Satellite range rate in m/s (positive = approaching)
            
        Returns:
            Corrected frequency in Hz
        """
        adjusted = self.frequency_hz * (1 - satellite_velocity_ms / SPEED_OF_LIGHT)
        self.sdr.center_freq = adjusted
        self.current_freq = adjusted
        shift = adjusted - self.frequency_hz
        
        logger.info('Doppler — velocity=%.1f m/s shift=%.1f Hz new_freq=%.4f MHz',
                   satellite_velocity_ms, shift, adjusted / 1e6)
        print(f'\033[92m[RTLSDR] Doppler — velocity={satellite_velocity_ms:.1f} m/s '
              f'shift={shift:.1f} Hz new_freq={adjusted/1e6:.4f} MHz\033[0m')
        return adjusted

    def read_frame(self, 
                   satellite_velocity_ms: Optional[float] = None,
                   norad_id: Optional[int] = None,
                   satellite_name: Optional[str] = None,
                   elevation_deg: float = 0.0,
                   azimuth_deg: float = 0.0,
                   range_km: float = 0.0) -> RFFrame:
        """
        Read a complete RF frame with all telemetry.
        
        This is the primary interface for the RF service. It produces
        a structured RFFrame object with all relevant RF metrics.
        
        Args:
            satellite_velocity_ms: Satellite range rate for Doppler correction
            norad_id: NORAD catalog ID
            satellite_name: Satellite name
            elevation_deg: Satellite elevation in degrees
            azimuth_deg: Satellite azimuth in degrees
            range_km: Range to satellite in km
            
        Returns:
            RFFrame object with comprehensive RF telemetry
        """
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        
        try:
            # Read samples
            samples = self.read_samples()
            
            # Compute metrics
            signal_dbm = self.compute_signal_dbm(samples)
            snr_db = self.compute_snr(samples)
            freqs_hz, power_db = self.compute_spectrum(samples)
            
            # Apply Doppler correction if velocity provided
            doppler_hz = 0.0
            if satellite_velocity_ms is not None:
                corrected_freq = self.apply_doppler_correction(satellite_velocity_ms)
                doppler_hz = corrected_freq - self.frequency_hz
            
            # Determine if receiving based on SNR threshold
            receiving = snr_db > 5.0
            
            # Estimate noise floor (use lower percentile of spectrum)
            noise_floor_dbm = float(np.percentile(power_db, 10)) if power_db else -100.0
            
            # Create frame
            frame = RFFrame(
                timestamp=datetime.now(timezone.utc).isoformat(),
                sequence=sequence,
                source_node=RFNodeType.PI2_RF_STATION,
                frequency_hz=self.current_freq,
                sample_rate=self.sample_rate,
                gain=self.gain,
                signal_dbm=signal_dbm,
                snr_db=snr_db,
                noise_floor_dbm=noise_floor_dbm,
                doppler_hz=doppler_hz,
                satellite_velocity_ms=satellite_velocity_ms,
                spectrum=power_db[:100],  # Limit spectrum size for transmission
                spectrum_freqs=freqs_hz[:100],
                norad_id=norad_id,
                satellite_name=satellite_name,
                elevation_deg=elevation_deg,
                azimuth_deg=azimuth_deg,
                range_km=range_km,
                rf_health=RFHealthStatus.ONLINE,
                mode=RFMode.ACQUISITION,
                frame_quality=min(1.0, max(0.0, (snr_db - 3.0) / 20.0)),  # Quality based on SNR
                receiving=receiving
            )
            
            logger.debug('Generated frame %d: signal=%.1f dBm SNR=%.1f dB', 
                        sequence, signal_dbm, snr_db)
            return frame
            
        except Exception as e:
            logger.error('Frame generation failed: %s', e)
            # Return error frame instead of raising
            return RFFrame(
                timestamp=datetime.now(timezone.utc).isoformat(),
                sequence=sequence,
                source_node=RFNodeType.PI2_RF_STATION,
                frequency_hz=self.current_freq,
                sample_rate=self.sample_rate,
                gain=self.gain,
                signal_dbm=-100.0,
                snr_db=0.0,
                noise_floor_dbm=-100.0,
                rf_health=RFHealthStatus.ERROR,
                mode=RFMode.ERROR,
                frame_quality=0.0,
                receiving=False
            )

    def close(self):
        """Close RTL-SDR device and release resources."""
        try:
            if hasattr(self, 'sdr') and self.sdr:
                self.sdr.close()
            logger.info('RTL-SDR closed')
            print('\033[92m[RTLSDR] Device closed\033[0m')
        except Exception as e:
            logger.warning('Error closing RTL-SDR: %s', e)


class MockRTLSDRReader:
    """
    Mock RTL-SDR reader for development without hardware.
    
    Simulates realistic RF data for testing and development.
    """

    def __init__(self,
                 frequency_hz: float = DEFAULT_FREQUENCY_HZ,
                 sample_rate: int = DEFAULT_SAMPLE_RATE,
                 gain: float = DEFAULT_GAIN):
        self.frequency_hz = frequency_hz
        self.sample_rate = sample_rate
        self.gain = gain
        self.current_freq = frequency_hz
        self._sequence = 0
        self._lock = threading.Lock()
        
        logger.warning('MockRTLSDRReader active — no real device')
        print('\033[93m[MOCK] No antenna — simulated signal\033[0m')

    def read_samples(self, num_samples: int = DEFAULT_BUFFER_SIZE) -> np.ndarray:
        """Generate mock IQ samples with simulated signal."""
        noise = (np.random.randn(num_samples) + 1j * np.random.randn(num_samples)) * 0.1
        t = np.arange(num_samples) / self.sample_rate
        signal = 0.3 * np.exp(2j * np.pi * 1000 * t)
        samples = noise + signal
        logger.debug('Mock: generated %d samples', num_samples)
        return samples

    def compute_snr(self, samples: np.ndarray) -> float:
        """Return mock SNR with realistic variation."""
        snr = round(np.random.uniform(8.0, 12.0), 2)
        logger.debug('Mock SNR=%.2f dB', snr)
        return snr

    def compute_signal_dbm(self, samples: np.ndarray) -> float:
        """Return mock signal strength with realistic variation."""
        dbm = round(np.random.uniform(-75.0, -65.0), 2)
        logger.debug('Mock signal=%.2f dBm', dbm)
        return dbm

    def apply_doppler_correction(self, satellite_velocity_ms: float) -> float:
        """Apply mock Doppler correction."""
        adjusted = self.frequency_hz * (1 - satellite_velocity_ms / SPEED_OF_LIGHT)
        self.current_freq = adjusted
        shift = adjusted - self.frequency_hz
        logger.info('Mock Doppler — velocity=%.1f m/s shift=%.1f Hz', satellite_velocity_ms, shift)
        print(f'\033[93m[MOCK] Doppler — velocity={satellite_velocity_ms:.1f} m/s '
              f'shift={shift:.1f} Hz new_freq={adjusted/1e6:.4f} MHz\033[0m')
        return adjusted

    def read_frame(self,
                   satellite_velocity_ms: Optional[float] = None,
                   norad_id: Optional[int] = None,
                   satellite_name: Optional[str] = None,
                   elevation_deg: float = 0.0,
                   azimuth_deg: float = 0.0,
                   range_km: float = 0.0) -> RFFrame:
        """Generate mock RF frame with realistic telemetry."""
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        
        samples = self.read_samples()
        signal_dbm = self.compute_signal_dbm(samples)
        snr_db = self.compute_snr(samples)
        
        # Mock Doppler
        doppler_hz = 0.0
        if satellite_velocity_ms is not None:
            corrected_freq = self.apply_doppler_correction(satellite_velocity_ms)
            doppler_hz = corrected_freq - self.frequency_hz
        
        # Mock spectrum
        freqs = np.linspace(self.current_freq - 1e6, self.current_freq + 1e6, 100)
        power = np.random.uniform(-85, -70, 100)
        
        return RFFrame(
            timestamp=datetime.now(timezone.utc).isoformat(),
            sequence=sequence,
            source_node=RFNodeType.MOCK,
            frequency_hz=self.current_freq,
            sample_rate=self.sample_rate,
            gain=self.gain,
            signal_dbm=signal_dbm,
            snr_db=snr_db,
            noise_floor_dbm=-85.0,
            doppler_hz=doppler_hz,
            satellite_velocity_ms=satellite_velocity_ms,
            spectrum=power.tolist(),
            spectrum_freqs=freqs.tolist(),
            norad_id=norad_id,
            satellite_name=satellite_name,
            elevation_deg=elevation_deg,
            azimuth_deg=azimuth_deg,
            range_km=range_km,
            rf_health=RFHealthStatus.ONLINE,
            mode=RFMode.ACQUISITION,
            frame_quality=0.8,
            receiving=snr_db > 5.0
        )

    def close(self):
        """Close mock reader."""
        logger.info('Mock RTL-SDR closed')
        print('\033[93m[MOCK] Reader closed\033[0m')


def get_reader(mock: bool = False,
                frequency_hz: float = DEFAULT_FREQUENCY_HZ,
                sample_rate: int = DEFAULT_SAMPLE_RATE,
                gain: float = DEFAULT_GAIN) -> RTLSDRReader:
    """
    Get RTL-SDR reader instance.
    
    Args:
        mock: Force mock mode even if hardware available
        frequency_hz: Center frequency in Hz
        sample_rate: Sample rate in Hz
        gain: RF gain in dB
        
    Returns:
        RTLSDRReader instance (real or mock)
    """
    if mock:
        logger.info('Mock mode forced')
        return MockRTLSDRReader(frequency_hz, sample_rate, gain)
    
    try:
        reader = RTLSDRReader(frequency_hz, sample_rate, gain)
        print('\033[92m[RTLSDR] Real device detected ✅\033[0m')
        return reader
    except Exception as e:
        logger.warning('RTL-SDR not available (%s) — falling back to mock', e)
        print(f'\033[93m[RTLSDR] Device not available — using mock\033[0m')
        return MockRTLSDRReader(frequency_hz, sample_rate, gain)


if __name__ == '__main__':
    print('\n=== RTL-SDR Reader Test ===\n')
    
    # Test with auto-detection
    reader = get_reader()
    
    print('\n--- Reading RF frame ---')
    frame = reader.read_frame(
        satellite_velocity_ms=7500.0,
        norad_id=59051,
        satellite_name="Meteor-M2-4",
        elevation_deg=45.0,
        azimuth_deg=180.0,
        range_km=1200.0
    )
    
    print(f'Frame sequence: {frame.sequence}')
    print(f'Frequency: {frame.frequency_hz/1e6:.3f} MHz')
    print(f'Signal: {frame.signal_dbm:.1f} dBm')
    print(f'SNR: {frame.snr_db:.1f} dB')
    print(f'Doppler: {frame.doppler_hz:+.1f} Hz')
    print(f'Receiving: {frame.receiving}')
    print(f'Health: {frame.rf_health}')
    
    reader.close()
    print('\n=== Test complete ===')
