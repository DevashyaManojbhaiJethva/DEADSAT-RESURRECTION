"""
RF Service — DeadSat Resurrection Pi #2
=======================================

Headless FastAPI service for Raspberry Pi #2 RF acquisition node.

This service:
- Runs on Raspberry Pi #2 with RTL-SDR hardware
- Provides REST endpoints for RF status and control
- Acquires RF data continuously using RTL-SDR
- Produces structured RFFrame objects
- Can be configured via environment variables
- Runs headlessly (no GUI/TkAgg dependencies)

Architecture:
    Antenna → RTL-SDR → RF Service → Network → Pi #1 Core
"""

import os
import sys
import asyncio
import threading
import time
import logging
from datetime import datetime, timezone
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import uvicorn

# Add parent directory to path for imports
sys.path.append(str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from rf.rtlsdr_reader import get_reader, DEFAULT_FREQUENCY_HZ, DEFAULT_SAMPLE_RATE, DEFAULT_GAIN
from rf.models import (
    RFFrame, RFStatusResponse, RFControlRequest, 
    RFHealthStatus, RFMode, RFNodeType
)
from rf.meteor_predictor import MeteorPredictor

# Import config from parent directory
import config as cfg

# Configuration from config.py
RF_SERVICE_HOST = cfg.RF_HOST
RF_SERVICE_PORT = cfg.RF_PORT
RF_CENTER_FREQUENCY_HZ = cfg.RF_CENTER_FREQUENCY_HZ
RF_SAMPLE_RATE = cfg.RF_SAMPLE_RATE
RF_GAIN = cfg.RF_GAIN
RF_STREAM_INTERVAL_S = cfg.RF_STREAM_INTERVAL_S
RF_MOCK_MODE = cfg.RF_MOCK_MODE

# Ground station location (configurable for deployment)
GROUND_LAT = cfg.GROUND_LAT
GROUND_LON = cfg.GROUND_LON
GROUND_ELEV_M = cfg.GROUND_ELEV_M

# Satellite tracking
DEFAULT_NORAD_ID = cfg.DEFAULT_NORAD_ID  # Meteor-M2-4

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s'
)
logger = logging.getLogger('rf_service')

# Global state
_reader = None
_predictor = None
_current_frame: Optional[RFFrame] = None
_service_start_time = datetime.now(timezone.utc)
_running = False
_frame_lock = threading.Lock()


def green(msg):  print(f"\033[92m{msg}\033[0m")
def yellow(msg): print(f"\033[93m{msg}\033[0m")
def red(msg):    print(f"\033[91m{msg}\033[0m")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    global _reader, _predictor, _running
    
    logger.info("Starting RF Service on %s:%s", RF_SERVICE_HOST, RF_SERVICE_PORT)
    green(f"[RF SERVICE] Starting on {RF_SERVICE_HOST}:{RF_SERVICE_PORT}")
    
    # Initialize RTL-SDR reader
    try:
        _reader = get_reader(
            mock=RF_MOCK_MODE,
            frequency_hz=RF_CENTER_FREQUENCY_HZ,
            sample_rate=RF_SAMPLE_RATE,
            gain=RF_GAIN
        )
        green(f"[RF SERVICE] RTL-SDR reader initialized ({'MOCK' if RF_MOCK_MODE else 'REAL'})")
    except Exception as e:
        logger.error("Failed to initialize RTL-SDR reader: %s", e)
        red(f"[RF SERVICE] Failed to initialize RTL-SDR reader: {e}")
        # Continue with mock reader as fallback
        _reader = get_reader(mock=True, frequency_hz=RF_CENTER_FREQUENCY_HZ, 
                            sample_rate=RF_SAMPLE_RATE, gain=RF_GAIN)
        yellow("[RF SERVICE] Using mock reader as fallback")
    
    # Initialize satellite predictor
    try:
        _predictor = MeteorPredictor(norad_id=DEFAULT_NORAD_ID)
        green(f"[RF SERVICE] Satellite predictor initialized for NORAD {DEFAULT_NORAD_ID}")
    except Exception as e:
        logger.warning("Failed to initialize satellite predictor: %s", e)
        yellow(f"[RF SERVICE] Satellite predictor failed: {e}")
        _predictor = None
    
    # Start background frame acquisition
    _running = True
    acquisition_thread = threading.Thread(target=_acquisition_loop, daemon=True)
    acquisition_thread.start()
    green("[RF SERVICE] Background RF acquisition started")
    
    # Print banner
    _print_banner()
    
    yield
    
    # Shutdown
    _running = False
    if _reader:
        try:
            _reader.close()
            green("[RF SERVICE] RTL-SDR reader closed")
        except Exception as e:
            logger.warning("Error closing RTL-SDR reader: %s", e)
    
    logger.info("RF Service shutdown complete")


def _print_banner():
    """Print startup banner with configuration."""
    print("\n" + "=" * 60)
    green("  DeadSat Resurrection — RF Service (Pi #2)")
    print("=" * 60)
    print(f"  Host:           {RF_SERVICE_HOST}:{RF_SERVICE_PORT}")
    print(f"  Frequency:      {RF_CENTER_FREQUENCY_HZ/1e6:.3f} MHz")
    print(f"  Sample Rate:    {RF_SAMPLE_RATE/1e6:.2f} MSPS")
    print(f"  Gain:           {RF_GAIN:.1f} dB")
    print(f"  Stream Interval: {RF_STREAM_INTERVAL_S:.1f}s")
    print(f"  Mode:           {'MOCK' if RF_MOCK_MODE else 'REAL'}")
    print(f"  Ground Station: {GROUND_LAT}°N, {GROUND_LON}°E, {GROUND_ELEV_M}m")
    print(f"  Target NORAD:   {DEFAULT_NORAD_ID}")
    print("=" * 60 + "\n")


def _acquisition_loop():
    """
    Background thread that continuously acquires RF frames.
    
    This runs independently of the FastAPI event loop, ensuring RF
    acquisition continues even if HTTP requests are slow.
    """
    global _current_frame, _running
    
    logger.info("RF acquisition loop started")
    
    while _running:
        try:
            # Get satellite tracking data if predictor available
            velocity_ms = None
            elevation_deg = 0.0
            azimuth_deg = 0.0
            range_km = 0.0
            sat_name = None
            
            if _predictor:
                try:
                    position = _predictor.get_current_position()
                    velocity_ms = _predictor.get_range_velocity()
                    elevation_deg = position.get('elevation_deg', 0.0)
                    azimuth_deg = position.get('azimuth_deg', 0.0)
                    range_km = position.get('range_km', 0.0)
                    sat_name = _predictor.sat_name
                except Exception as e:
                    logger.debug("Satellite tracking error: %s", e)
            
            # Read RF frame
            frame = _reader.read_frame(
                satellite_velocity_ms=velocity_ms,
                norad_id=DEFAULT_NORAD_ID,
                satellite_name=sat_name,
                elevation_deg=elevation_deg,
                azimuth_deg=azimuth_deg,
                range_km=range_km
            )
            
            # Update current frame (thread-safe)
            with _frame_lock:
                _current_frame = frame
            
            logger.debug("Acquired frame %d: signal=%.1f dBm SNR=%.1f dB", 
                        frame.sequence, frame.signal_dbm, frame.snr_db)
            
        except Exception as e:
            logger.error("RF acquisition error: %s", e)
            # Create error frame to indicate failure
            with _frame_lock:
                _current_frame = RFFrame(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    sequence=0,
                    source_node=RFNodeType.PI2_RF_STATION,
                    frequency_hz=RF_CENTER_FREQUENCY_HZ,
                    sample_rate=RF_SAMPLE_RATE,
                    gain=RF_GAIN,
                    signal_dbm=-100.0,
                    snr_db=0.0,
                    noise_floor_dbm=-100.0,
                    rf_health=RFHealthStatus.ERROR,
                    mode=RFMode.ERROR,
                    frame_quality=0.0,
                    receiving=False
                )
        
        # Sleep for configured interval
        time.sleep(RF_STREAM_INTERVAL_S)


# Create FastAPI app
app = FastAPI(
    title="DeadSat RF Service",
    description="RF acquisition service for Raspberry Pi #2",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    
    Returns basic service health status for monitoring and load balancers.
    """
    uptime = (datetime.now(timezone.utc) - _service_start_time).total_seconds()
    return {
        "status": "healthy",
        "uptime_seconds": uptime,
        "service": "rf_service",
        "version": "1.0.0"
    }


@app.get("/rf/status")
async def get_rf_status():
    """
    Get current RF status and latest frame.
    
    Returns comprehensive RF status including the most recent frame,
    device availability, and service health.
    """
    with _frame_lock:
        frame = _current_frame
    
    uptime = (datetime.now(timezone.utc) - _service_start_time).total_seconds()
    device_available = not RF_MOCK_MODE
    
    # Determine health status
    if frame and frame.rf_health == RFHealthStatus.ERROR:
        health = RFHealthStatus.ERROR
        error_msg = "RF acquisition error"
    elif frame and frame.rf_health == RFHealthStatus.DEGRADED:
        health = RFHealthStatus.DEGRADED
        error_msg = "RF acquisition degraded"
    else:
        health = RFHealthStatus.ONLINE
        error_msg = None
    
    return RFStatusResponse(
        online=True,
        mode=frame.mode if frame else RFMode.IDLE,
        health=health,
        current_frame=frame,
        uptime_seconds=uptime,
        device_available=device_available,
        error_message=error_msg
    )


@app.get("/rf/spectrum")
async def get_rf_spectrum():
    """
    Get current RF spectrum data.
    
    Returns the power spectrum from the most recent frame.
    Useful for visualization and signal analysis.
    """
    with _frame_lock:
        frame = _current_frame
    
    if not frame:
        raise HTTPException(status_code=503, detail="No RF data available yet")
    
    return {
        "timestamp": frame.timestamp,
        "sequence": frame.sequence,
        "frequency_hz": frame.frequency_hz,
        "spectrum": frame.spectrum,
        "spectrum_freqs": frame.spectrum_freqs,
        "signal_dbm": frame.signal_dbm,
        "snr_db": frame.snr_db,
        "receiving": frame.receiving
    }


@app.post("/rf/control")
async def control_rf(request: RFControlRequest):
    """
    Control RF parameters.
    
    Allows dynamic adjustment of frequency, gain, sample rate, and mode.
    Changes take effect on the next frame acquisition.
    """
    global _reader
    
    try:
        # Apply parameter changes
        if request.frequency_hz is not None:
            if hasattr(_reader, 'frequency_hz'):
                _reader.frequency_hz = request.frequency_hz
            if hasattr(_reader, 'sdr'):
                _reader.sdr.center_freq = request.frequency_hz
            logger.info("Frequency changed to %.3f MHz", request.frequency_hz / 1e6)
        
        if request.gain is not None:
            if hasattr(_reader, 'gain'):
                _reader.gain = request.gain
            if hasattr(_reader, 'sdr'):
                _reader.sdr.gain = request.gain
            logger.info("Gain changed to %.1f dB", request.gain)
        
        if request.sample_rate is not None:
            if hasattr(_reader, 'sample_rate'):
                _reader.sample_rate = request.sample_rate
            if hasattr(_reader, 'sdr'):
                _reader.sdr.sample_rate = request.sample_rate
            logger.info("Sample rate changed to %.1f MSPS", request.sample_rate / 1e6)
        
        if request.mode is not None:
            logger.info("Mode change requested to %s", request.mode)
            # Mode changes would require more complex state management
            # For now, just log the request
        
        return {
            "status": "success",
            "message": "RF parameters updated",
            "applied_changes": {
                "frequency_hz": request.frequency_hz,
                "gain": request.gain,
                "sample_rate": request.sample_rate,
                "mode": request.mode
            }
        }
        
    except Exception as e:
        logger.error("RF control failed: %s", e)
        raise HTTPException(status_code=500, detail=f"RF control failed: {e}")


@app.get("/rf/config")
async def get_rf_config():
    """
    Get current RF configuration.
    
    Returns the active configuration parameters for monitoring
    and debugging.
    """
    return {
        "service": {
            "host": RF_SERVICE_HOST,
            "port": RF_SERVICE_PORT,
            "mock_mode": RF_MOCK_MODE,
            "stream_interval_s": RF_STREAM_INTERVAL_S
        },
        "rf_parameters": {
            "frequency_hz": RF_CENTER_FREQUENCY_HZ,
            "sample_rate": RF_SAMPLE_RATE,
            "gain": RF_GAIN
        },
        "ground_station": {
            "latitude_deg": GROUND_LAT,
            "longitude_deg": GROUND_LON,
            "elevation_m": GROUND_ELEV_M
        },
        "satellite": {
            "target_norad_id": DEFAULT_NORAD_ID
        }
    }


def main():
    """Main entry point for running the RF service."""
    green(f"\n[RF SERVICE] Starting DeadSat RF Service on {RF_SERVICE_HOST}:{RF_SERVICE_PORT}")
    uvicorn.run(
        "rf.service:app",
        host=RF_SERVICE_HOST,
        port=RF_SERVICE_PORT,
        log_level="info",
        access_log=False  # Reduce log noise
    )


if __name__ == "__main__":
    main()