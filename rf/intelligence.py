"""
RF Intelligence — DeadSat Resurrection
====================================

Extracts intelligence features from RF frames for the AI pipeline.

This module:
- Processes RF frames to extract meaningful features
- Detects anomalies and patterns in RF data
- Produces structured intelligence data for AI-1 and AI-2
- Monitors RF health and signal quality
- Generates alerts for RF-related issues

Architecture:
    RF Frame → RF Intelligence → Features → AI Pipeline
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from collections import deque
import statistics

from rf.models import RFFrame, RFHealthStatus, RFMode

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s'
)
logger = logging.getLogger('rf_intelligence')


class RFIntelligence:
    """
    Extracts intelligence features from RF frames.
    
    Analyzes RF data patterns, detects anomalies, and produces
    structured intelligence for the AI pipeline.
    """

    def __init__(self, history_size: int = 60):
        """
        Initialize RF intelligence processor.
        
        Args:
            history_size: Number of frames to keep in history for analysis
        """
        self.history_size = history_size
        self._frame_history = deque(maxlen=history_size)  # type: deque
        self._lock = threading.Lock()
        
        # Anomaly detection thresholds
        self._signal_baseline = None
        self._snr_baseline = None
        self._baseline_samples = 0
        
        # Alert state
        self._active_alerts: List[str] = []
        
        logger.info("RF Intelligence initialized with history size %d", history_size)

    def process_frame(self, frame: RFFrame) -> Dict[str, Any]:
        """
        Process an RF frame and extract intelligence features.
        
        Args:
            frame: RF frame to process
            
        Returns:
            Dictionary of intelligence features
        """
        with self._lock:
            self._frame_history.append(frame)
            
            # Update baselines
            self._update_baselines(frame)
            
            # Extract features
            features = {
                "timestamp": frame.timestamp,
                "sequence": frame.sequence,
                "signal_strength": frame.signal_dbm,
                "snr": frame.snr_db,
                "doppler_shift": frame.doppler_hz,
                "frequency": frame.frequency_hz,
                "receiving": frame.receiving,
                "frame_quality": frame.frame_quality,
                "rf_health": frame.rf_health.value,
                
                # Derived features
                "signal_trend": self._compute_signal_trend(),
                "snr_trend": self._compute_snr_trend(),
                "doppler_rate": self._compute_doppler_rate(),
                "signal_stability": self._compute_signal_stability(),
                "noise_floor": frame.noise_floor_dbm,
                
                # Anomaly detection
                "signal_anomaly": self._detect_signal_anomaly(frame.signal_dbm),
                "snr_anomaly": self._detect_snr_anomaly(frame.snr_db),
                "doppler_anomaly": self._detect_doppler_anomaly(frame.doppler_hz),
                
                # Satellite tracking info
                "elevation": frame.elevation_deg,
                "azimuth": frame.azimuth_deg,
                "range": frame.range_km,
                "satellite_visible": frame.elevation_deg > 10.0,
                
                # System health
                "rf_health_status": frame.rf_health.value,
                "mode": frame.mode.value,
                "device_available": frame.rf_health == RFHealthStatus.ONLINE,
                
                # Alerts
                "active_alerts": self._generate_alerts(frame),
                
                # Connection quality
                "frame_rate": self._compute_frame_rate(),
                "connection_quality": self._assess_connection_quality(),
            }
            
            logger.debug("Processed RF frame %d: %d features extracted", 
                        frame.sequence, len(features))
            return features

    def _update_baselines(self, frame: RFFrame):
        """Update baseline values for anomaly detection."""
        if self._signal_baseline is None:
            self._signal_baseline = frame.signal_dbm
            self._snr_baseline = frame.snr_db
            self._baseline_samples = 1
        else:
            # Exponential moving average
            alpha = 0.1
            self._signal_baseline = (alpha * frame.signal_dbm + 
                                   (1 - alpha) * self._signal_baseline)
            self._snr_baseline = (alpha * frame.snr_db + 
                                (1 - alpha) * self._snr_baseline)
            self._baseline_samples += 1

    def _compute_signal_trend(self) -> str:
        """Compute signal strength trend."""
        if len(self._frame_history) < 5:
            return "unknown"
        
        recent = [f.signal_dbm for f in list(self._frame_history)[-5:]]
        if recent[-1] > recent[0] + 2:
            return "increasing"
        elif recent[-1] < recent[0] - 2:
            return "decreasing"
        else:
            return "stable"

    def _compute_snr_trend(self) -> str:
        """Compute SNR trend."""
        if len(self._frame_history) < 5:
            return "unknown"
        
        recent = [f.snr_db for f in list(self._frame_history)[-5:]]
        if recent[-1] > recent[0] + 1:
            return "improving"
        elif recent[-1] < recent[0] - 1:
            return "degrading"
        else:
            return "stable"

    def _compute_doppler_rate(self) -> float:
        """Compute rate of Doppler change (Hz/s)."""
        if len(self._frame_history) < 2:
            return 0.0
        
        recent = list(self._frame_history)[-2:]
        time_diff = 1.0  # Assume 1 second between frames
        doppler_diff = recent[-1].doppler_hz - recent[0].doppler_hz
        
        return round(doppler_diff / time_diff, 2)

    def _compute_signal_stability(self) -> float:
        """Compute signal stability (lower = more stable)."""
        if len(self._frame_history) < 10:
            return 0.0
        
        recent = [f.signal_dbm for f in list(self._frame_history)[-10:]]
        if len(recent) < 2:
            return 0.0
        
        variance = statistics.variance(recent)
        return round(float(variance), 2)

    def _detect_signal_anomaly(self, current_signal: float) -> bool:
        """Detect if current signal is anomalous."""
        if self._signal_baseline is None or self._baseline_samples < 10:
            return False
        
        threshold = 10.0  # dB deviation threshold
        return abs(current_signal - self._signal_baseline) > threshold

    def _detect_snr_anomaly(self, current_snr: float) -> bool:
        """Detect if current SNR is anomalous."""
        if self._snr_baseline is None or self._baseline_samples < 10:
            return False
        
        threshold = 5.0  # dB deviation threshold
        return abs(current_snr - self._snr_baseline) > threshold

    def _detect_doppler_anomaly(self, current_doppler: float) -> bool:
        """Detect if current Doppler is anomalous."""
        # Doppler > 10 kHz is unusual for LEO satellites
        return abs(current_doppler) > 10000.0

    def _generate_alerts(self, frame: RFFrame) -> List[str]:
        """Generate alerts based on RF frame analysis."""
        alerts = []
        
        # Health alerts
        if frame.rf_health == RFHealthStatus.ERROR:
            alerts.append("RF_SYSTEM_ERROR")
        elif frame.rf_health == RFHealthStatus.DEGRADED:
            alerts.append("RF_SYSTEM_DEGRADED")
        
        # Signal alerts
        if frame.signal_dbm < -90:
            alerts.append("WEAK_SIGNAL")
        elif frame.signal_dbm > -50:
            alerts.append("STRONG_SIGNAL")
        
        # SNR alerts
        if frame.snr_db < 3:
            alerts.append("POOR_SNR")
        
        # Receiving alerts
        if frame.receiving and frame.snr_db < 5:
            alerts.append("MARGINAL_RECEPTION")
        
        # Satellite visibility
        if frame.elevation_deg > 10 and not frame.receiving:
            alerts.append("SATELLITE_VISIBLE_NO_SIGNAL")
        
        # Anomaly alerts
        if self._detect_signal_anomaly(frame.signal_dbm):
            alerts.append("SIGNAL_ANOMALY")
        if self._detect_snr_anomaly(frame.snr_db):
            alerts.append("SNR_ANOMALY")
        if self._detect_doppler_anomaly(frame.doppler_hz):
            alerts.append("DOPPLER_ANOMALY")
        
        return alerts

    def _compute_frame_rate(self) -> float:
        """Compute current frame rate (frames/second)."""
        if len(self._frame_history) < 2:
            return 0.0
        
        # Calculate based on timestamps of recent frames
        recent = list(self._frame_history)[-10:]
        if len(recent) < 2:
            return 0.0
        
        try:
            times = [datetime.fromisoformat(f.timestamp.replace('Z', '+00:00')) 
                     for f in recent]
            time_span = (times[-1] - times[0]).total_seconds()
            if time_span > 0:
                return round(len(recent) / time_span, 2)
        except Exception:
            pass
        
        return 0.0

    def _assess_connection_quality(self) -> str:
        """Assess overall RF connection quality."""
        if len(self._frame_history) < 5:
            return "unknown"
        
        recent = list(self._frame_history)[-5:]
        
        # Check for errors
        error_count = sum(1 for f in recent if f.rf_health == RFHealthStatus.ERROR)
        if error_count > 2:
            return "poor"
        
        # Check frame quality
        avg_quality = sum(f.frame_quality for f in recent) / len(recent)
        if avg_quality > 0.8:
            return "excellent"
        elif avg_quality > 0.5:
            return "good"
        elif avg_quality > 0.3:
            return "fair"
        else:
            return "poor"

    def get_latest_intelligence(self) -> Optional[Dict[str, Any]]:
        """Get the latest intelligence data."""
        with self._lock:
            if not self._frame_history:
                return None
            return self.process_frame(self._frame_history[-1])

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of RF intelligence state."""
        with self._lock:
            if not self._frame_history:
                return {
                    "status": "no_data",
                    "frames_processed": 0,
                    "active_alerts": []
                }
            
            latest = self._frame_history[-1]
            intelligence = self.process_frame(latest)
            
            return {
                "status": "active",
                "frames_processed": len(self._frame_history),
                "latest_sequence": latest.sequence,
                "latest_timestamp": latest.timestamp,
                "receiving": latest.receiving,
                "signal_strength": latest.signal_dbm,
                "snr": latest.snr_db,
                "rf_health": latest.rf_health.value,
                "active_alerts": intelligence["active_alerts"],
                "connection_quality": intelligence["connection_quality"],
                "baseline_signal": self._signal_baseline,
                "baseline_snr": self._snr_baseline,
            }


# Global RF intelligence instance
_rf_intelligence: Optional[RFIntelligence] = None
_intelligence_lock = threading.Lock()


def get_rf_intelligence(history_size: int = 60) -> RFIntelligence:
    """Get or create the global RF intelligence instance."""
    global _rf_intelligence
    
    with _intelligence_lock:
        if _rf_intelligence is None:
            _rf_intelligence = RFIntelligence(history_size)
        return _rf_intelligence


def process_rf_frame_for_intelligence(frame: RFFrame) -> Dict[str, Any]:
    """
    Process an RF frame through the intelligence pipeline.
    
    This is the main entry point for integrating RF data with the AI pipeline.
    
    Args:
        frame: RF frame to process
        
    Returns:
        Intelligence features dictionary
    """
    intelligence = get_rf_intelligence()
    return intelligence.process_frame(frame)