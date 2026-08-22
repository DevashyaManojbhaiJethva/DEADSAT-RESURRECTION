"""
RF Transport — DeadSat Resurrection
===================================

Handles network transport of RF frames from Pi #2 to Pi #1.

This module provides:
- Reliable HTTP POST of RF frames to Pi #1 ingest endpoint
- Reconnection logic and error handling
- Sequence number tracking
- Timeout handling
- Backpressure management

Architecture:
    Pi #2 RF Service → RF Transport → HTTP POST → Pi #1 /rf/ingest
"""

import asyncio
import aiohttp
import logging
import time
from typing import Optional, Tuple
from datetime import datetime, timezone
import json

from rf.models import RFFrame, RFIngestRequest, RFIngestResponse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s'
)
logger = logging.getLogger('rf_transport')


class RFTransportError(Exception):
    """Base exception for RF transport errors."""
    pass


class ConnectionError(RFTransportError):
    """Connection failure to Pi #1."""
    pass


class ValidationError(RFTransportError):
    """Frame validation failed."""
    pass


class RFTransport:
    """
    Manages RF frame transmission from Pi #2 to Pi #1.
    
    Handles network communication with reconnection, error handling,
    and backpressure management.
    """

    def __init__(self, 
                 pi1_base_url: str,
                 api_key: Optional[str] = None,
                 timeout: float = 3.0,
                 max_retries: int = 3,
                 retry_delay: float = 1.0):
        """
        Initialize RF transport.
        
        Args:
            pi1_base_url: Base URL of Pi #1 (e.g., "http://192.168.1.50:8000")
            api_key: Optional API key for authentication
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds
        """
        self.pi1_base_url = pi1_base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        self.ingest_url = f"{self.pi1_base_url}/rf/ingest"
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_sequence = -1
        self._connectionhealthy = True
        self._last_success_time = None
        self._failure_count = 0
        
        logger.info("RF Transport initialized: %s", self.ingest_url)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy initialization of HTTP session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["X-API-Key"] = self.api_key
            
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers=headers
            )
        return self._session

    async def _close_session(self):
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _validate_frame(self, frame: RFFrame):
        """
        Validate RF frame before transmission.
        
        Args:
            frame: RF frame to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check sequence number monotonicity
        if frame.sequence <= self._last_sequence:
            return (False, f"Non-increasing sequence: {frame.sequence} <= {self._last_sequence}")
        
        # Check timestamp freshness (reject stale frames > 10 seconds old)
        try:
            frame_time = datetime.fromisoformat(frame.timestamp.replace('Z', '+00:00'))
            age = (datetime.now(timezone.utc) - frame_time).total_seconds()
            if age > 10.0:
                return (False, f"Stale frame: {age:.1f}s old")
        except ValueError:
            return (False, f"Invalid timestamp format: {frame.timestamp}")
        
        # Check essential fields
        if frame.signal_dbm < -150 or frame.signal_dbm > -10:
            return (False, f"Invalid signal strength: {frame.signal_dbm} dBm")
        
        if frame.frequency_hz < 1e6 or frame.frequency_hz > 30e9:
            return (False, f"Invalid frequency: {frame.frequency_hz} Hz")
        
        return (True, None)

    async def send_frame(self, frame: RFFrame) -> RFIngestResponse:
        """
        Send RF frame to Pi #1 with retry logic.
        
        Args:
            frame: RF frame to transmit
            
        Returns:
            RFIngestResponse from Pi #1
            
        Raises:
            RFTransportError: If transmission fails after retries
        """
        # Validate frame
        is_valid, error_msg = self._validate_frame(frame)
        if not is_valid:
            logger.warning("Frame validation failed: %s", error_msg)
            raise ValidationError(error_msg)
        
        # Update sequence tracking
        self._last_sequence = frame.sequence
        
        # Prepare request
        request_data = RFIngestRequest(frame=frame, api_key=self.api_key)
        payload = request_data.model_dump_json(exclude_none=True)
        
        # Retry loop
        last_error = None
        for attempt in range(self.max_retries):
            try:
                session = await self._get_session()
                
                logger.debug("Sending frame %d to %s (attempt %d)", 
                           frame.sequence, self.ingest_url, attempt + 1)
                
                async with session.post(
                    self.ingest_url,
                    data=payload
                ) as response:
                    if response.status == 200:
                        response_data = await response.json()
                        ingest_response = RFIngestResponse(**response_data)
                        
                        # Update health tracking
                        self._connectionhealthy = True
                        self._last_success_time = datetime.now(timezone.utc)
                        self._failure_count = 0
                        
                        logger.info("Frame %d accepted: %s", frame.sequence, ingest_response.message)
                        return ingest_response
                    
                    elif response.status == 400:
                        # Validation error on Pi #1 - don't retry
                        error_data = await response.json()
                        logger.error("Pi #1 rejected frame %d: %s", frame.sequence, error_data.get('detail'))
                        raise ValidationError(f"Pi #1 validation failed: {error_data.get('detail')}")
                    
                    elif response.status == 401:
                        # Authentication error - don't retry
                        logger.error("Authentication failed to Pi #1")
                        raise ConnectionError("Authentication failed - check API key")
                    
                    elif response.status == 413:
                        # Payload too large - don't retry
                        logger.error("Frame payload too large")
                        raise ValidationError("Frame payload exceeds size limit")
                    
                    else:
                        # Other HTTP errors - retry
                        error_data = await response.text()
                        last_error = f"HTTP {response.status}: {error_data}"
                        logger.warning("Pi #1 returned %s (attempt %d)", last_error, attempt + 1)
                        
            except aiohttp.ClientError as e:
                last_error = f"Connection error: {e}"
                logger.warning("Connection error (attempt %d): %s", attempt + 1, e)
                self._connectionhealthy = False
                self._failure_count += 1
                
            except asyncio.TimeoutError:
                last_error = "Request timeout"
                logger.warning("Request timeout (attempt %d)", attempt + 1)
                self._connectionhealthy = False
                self._failure_count += 1
            
            # Wait before retry (except on last attempt)
            if attempt < self.max_retries - 1:
                await asyncio.sleep(self.retry_delay * (2 ** attempt))  # Exponential backoff
        
        # All retries failed
        self._connectionhealthy = False
        error_msg = f"Failed to send frame {frame.sequence} after {self.max_retries} attempts: {last_error}"
        logger.error(error_msg)
        raise ConnectionError(error_msg)

    async def send_frame_async_no_wait(self, frame: RFFrame, 
                                       error_callback=None):
        """
        Send RF frame without waiting for response (fire-and-forget).
        
        This is useful for high-frequency streaming where blocking on
        each transmission would cause backpressure issues.
        
        Args:
            frame: RF frame to transmit
            error_callback: Optional callback for transmission errors
        """
        try:
            await self.send_frame(frame)
        except Exception as e:
            logger.error("Async frame transmission failed: %s", e)
            if error_callback:
                error_callback(e)

    def is_healthy(self) -> bool:
        """Check if connection to Pi #1 is healthy."""
        return self._connectionhealthy

    def get_failure_count(self) -> int:
        """Get number of consecutive failures."""
        return self._failure_count

    def get_last_success_time(self) -> Optional[datetime]:
        """Get timestamp of last successful transmission."""
        return self._last_success_time

    async def close(self):
        """Clean up resources."""
        await self._close_session()
        logger.info("RF Transport closed")


class RFTransportStreamer:
    """
    High-level streaming interface for continuous RF frame transmission.
    
    Manages background transmission task with backpressure handling.
    """

    def __init__(self, transport: RFTransport, max_queue_size: int = 10):
        """
        Initialize RF transport streamer.
        
        Args:
            transport: RFTransport instance
            max_queue_size: Maximum number of frames to queue
        """
        self.transport = transport
        self.max_queue_size = max_queue_size
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._dropped_frames = 0

    async def start(self):
        """Start background transmission task."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._transmission_loop())
        logger.info("RF Transport streamer started")

    async def stop(self):
        """Stop background transmission task."""
        if not self._running:
            return
        
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        logger.info("RF Transport streamer stopped")

    async def _transmission_loop(self):
        """Background task that processes frame queue."""
        while self._running:
            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self.transport.send_frame(frame)
            except asyncio.TimeoutError:
                # No frames in queue - continue
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Transmission loop error: %s", e)
                # Continue processing other frames

    async def enqueue_frame(self, frame: RFFrame) -> bool:
        """
        Enqueue frame for transmission.
        
        Args:
            frame: RF frame to enqueue
            
        Returns:
            True if frame was enqueued, False if queue was full (dropped)
        """
        if self._queue.full():
            self._dropped_frames += 1
            logger.warning("Frame queue full, dropped frame %d (total dropped: %d)", 
                         frame.sequence, self._dropped_frames)
            return False
        
        try:
            self._queue.put_nowait(frame)
            return True
        except asyncio.QueueFull:
            self._dropped_frames += 1
            return False

    def get_queue_size(self) -> int:
        """Get current queue size."""
        return self._queue.qsize()

    def get_dropped_count(self) -> int:
        """Get number of dropped frames."""
        return self._dropped_frames


async def test_transport():
    """Test RF transport functionality."""
    import os
    
    # Test configuration
    pi1_url = os.environ.get("DEADSAT_RF_BASE", "http://localhost:8000")
    api_key = os.environ.get("DEADSAT_API_KEY")
    
    transport = RFTransport(pi1_url, api_key=api_key)
    
    # Create test frame
    from rf.models import RFNodeType, RFHealthStatus, RFMode
    test_frame = RFFrame(
        timestamp=datetime.now(timezone.utc).isoformat(),
        sequence=1,
        source_node=RFNodeType.PI2_RF_STATION,
        frequency_hz=137900000.0,
        sample_rate=2400000,
        gain=49.6,
        signal_dbm=-72.4,
        snr_db=9.7,
        rf_health=RFHealthStatus.ONLINE,
        mode=RFMode.ACQUISITION,
        receiving=True
    )
    
    try:
        response = await transport.send_frame(test_frame)
        print(f"Test successful: {response}")
    except Exception as e:
        print(f"Test failed: {e}")
    finally:
        await transport.close()


if __name__ == "__main__":
    asyncio.run(test_transport())