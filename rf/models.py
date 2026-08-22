"""
RF Data Models — DeadSat Resurrection
======================================

Defines the canonical RF frame schema for communication between Pi #2 (RF node)
and Pi #1 (Core node), as well as WebSocket streaming to the frontend.

Architecture:
    Pi #2 (RF) → RF Frame → Pi #1 (Core) → RF Intelligence → Frontend
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime, timezone
from enum import Enum


class RFNodeType(str, Enum):
    """Identifies which RF node produced the frame."""
    PI2_RF_STATION = "pi2_rf_station"
    EMULATOR = "emulator"
    MOCK = "mock"


class RFHealthStatus(str, Enum):
    """Health status of the RF acquisition system."""
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    ERROR = "error"


class RFMode(str, Enum):
    """Operational mode of the RF system."""
    # REAL is physical acquisition; MOCK is only an explicit controlled source.
    REAL = "real"
    MOCK = "mock"
    ACQUISITION = "acquisition"
    IDLE = "idle"
    CALIBRATION = "calibration"
    ERROR = "error"


class RFFrame(BaseModel):
    """
    Canonical RF frame schema transmitted from Pi #2 to Pi #1.
    
    This structure is designed to be lightweight but informative, containing
    essential RF telemetry without transmitting raw IQ data continuously.
    """
    # Metadata
    timestamp: str = Field(..., description="ISO 8601 timestamp in UTC")
    sequence: int = Field(..., description="Monotonically increasing sequence number")
    source_node: RFNodeType = Field(default=RFNodeType.PI2_RF_STATION)
    schema_version: str = Field(default="1.0", description="Schema version for compatibility")
    
    # RF Parameters
    frequency_hz: float = Field(..., description="Center frequency in Hz")
    sample_rate: int = Field(..., description="Sample rate in samples per second")
    gain: float = Field(..., description="RF gain in dB")
    
    # Signal Metrics
    signal_dbm: float = Field(..., description="Signal strength in dBm")
    snr_db: float = Field(..., description="Signal-to-noise ratio in dB")
    noise_floor_dbm: float = Field(default=-100.0, description="Noise floor in dBm")
    
    # Doppler & Motion
    doppler_hz: float = Field(default=0.0, description="Doppler shift in Hz")
    satellite_velocity_ms: Optional[float] = Field(None, description="Satellite range rate in m/s")
    
    # Spectrum Data (compressed features, not raw IQ)
    spectrum: List[float] = Field(default_factory=list, description="Power spectrum (dBm)")
    spectrum_freqs: List[float] = Field(default_factory=list, description="Frequency points for spectrum (Hz)")
    
    # Satellite Tracking (if available)
    norad_id: Optional[int] = Field(None, description="NORAD catalog ID")
    satellite_name: Optional[str] = Field(None, description="Satellite name")
    elevation_deg: float = Field(default=0.0, description="Satellite elevation in degrees")
    azimuth_deg: float = Field(default=0.0, description="Satellite azimuth in degrees")
    range_km: float = Field(default=0.0, description="Range to satellite in km")
    
    # Health & Status
    rf_health: RFHealthStatus = Field(default=RFHealthStatus.ONLINE)
    mode: RFMode = Field(default=RFMode.ACQUISITION)
    
    # Quality Indicators
    frame_quality: float = Field(default=1.0, ge=0.0, le=1.0, description="Frame quality score (0-1)")
    receiving: bool = Field(default=False, description="True if currently receiving satellite signal")
    
    @validator('timestamp')
    def validate_timestamp(cls, v):
        """Ensure timestamp is valid ISO format."""
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(f"Invalid timestamp format: {v}")
        return v
    
    @validator('sequence')
    def validate_sequence(cls, v):
        """Ensure sequence is non-negative."""
        if v < 0:
            raise ValueError("Sequence must be non-negative")
        return v
    
    @validator('frequency_hz')
    def validate_frequency(cls, v):
        """Ensure frequency is in reasonable range for satellite work."""
        if not (1e6 <= v <= 30e9):  # 1 MHz to 30 GHz
            raise ValueError(f"Frequency {v} Hz out of reasonable range")
        return v
    
    @validator('signal_dbm')
    def validate_signal_dbm(cls, v):
        """Ensure signal strength is in reasonable range."""
        if not (-150 <= v <= -10):  # Typical SDR range
            raise ValueError(f"Signal strength {v} dBm out of reasonable range")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2026-08-22T12:34:56.789Z",
                "sequence": 12345,
                "source_node": "pi2_rf_station",
                "schema_version": "1.0",
                "frequency_hz": 137900000.0,
                "sample_rate": 2400000,
                "gain": 49.6,
                "signal_dbm": -72.4,
                "snr_db": 9.7,
                "noise_floor_dbm": -82.1,
                "doppler_hz": -1240.0,
                "satellite_velocity_ms": 7500.0,
                "spectrum": [-80.5, -78.2, -75.1, -82.3],
                "spectrum_freqs": [137899000.0, 137899500.0, 137900000.0, 137900500.0],
                "norad_id": 59051,
                "satellite_name": "Meteor-M2-4",
                "elevation_deg": 45.2,
                "azimuth_deg": 180.5,
                "range_km": 1200.0,
                "rf_health": "online",
                "mode": "acquisition",
                "frame_quality": 0.95,
                "receiving": True
            }
        }


class RFControlRequest(BaseModel):
    """Request to control RF parameters on Pi #2."""
    frequency_hz: Optional[float] = Field(None, description="Center frequency in Hz")
    gain: Optional[float] = Field(None, description="RF gain in dB")
    sample_rate: Optional[int] = Field(None, description="Sample rate in Hz")
    mode: Optional[RFMode] = Field(None, description="Operational mode")
    
    @validator('frequency_hz')
    def validate_frequency(cls, v):
        if v is not None and not (1e6 <= v <= 30e9):
            raise ValueError(f"Frequency {v} Hz out of reasonable range")
        return v
    
    @validator('gain')
    def validate_gain(cls, v):
        if v is not None and not (0.0 <= v <= 50.0):
            raise ValueError(f"Gain {v} dB out of reasonable range")
        return v


class RFStatusResponse(BaseModel):
    """Status response from RF service."""
    online: bool = Field(..., description="Whether RF service is operational")
    mode: RFMode = Field(..., description="Current operational mode")
    health: RFHealthStatus = Field(..., description="Health status")
    current_frame: Optional[RFFrame] = Field(None, description="Most recent RF frame")
    uptime_seconds: float = Field(default=0.0, description="Service uptime in seconds")
    device_available: bool = Field(default=False, description="Whether RTL-SDR device is available")
    error_message: Optional[str] = Field(None, description="Error message if in error state")


class RFIngestRequest(BaseModel):
    """Request format for RF data ingestion at Pi #1."""
    frame: RFFrame = Field(..., description="RF frame to ingest")
    api_key: Optional[str] = Field(None, description="API key for authentication")


class RFIngestResponse(BaseModel):
    """Response from RF data ingestion."""
    accepted: bool = Field(..., description="Whether frame was accepted")
    sequence: int = Field(..., description="Sequence number of accepted frame")
    message: str = Field(..., description="Status message")
    warnings: List[str] = Field(default_factory=list, description="Any warnings about the frame")
