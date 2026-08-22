/**
 * api.ts — DeadSat Resurrection · backend client
 * ================================================
 * The single place the frontend talks to Pi #1.
 *
 * Before this existed the UI made no backend calls at all — every value on
 * the dashboard came from `Math.random()` in a `setInterval`, and the
 * "WebSocket client ... handshaking authorized" line was a hardcoded log
 * string rather than a connection. This module wires the React app to the
 * FastAPI service that the emulator, AI-1, AI-2 and the crypto layer run on.
 *
 * Deployment
 * ----------
 * Set `VITE_API_BASE` in `frontend/.env` to Pi #1's address:
 *
 *     VITE_API_BASE=http://192.168.1.50:8000
 *
 * If unset it falls back to the current page host on port 8000, which covers
 * running the dev server on the same machine as the backend.
 */

// ──────────────────────────────────────────────────────────────────────
// Configuration
// ──────────────────────────────────────────────────────────────────────

function resolveBase(): string {
  const configured = (import.meta as any).env?.VITE_API_BASE as string | undefined;
  if (configured && configured.trim()) return configured.trim().replace(/\/$/, '');
  if (typeof window !== 'undefined' && window.location?.hostname) {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return 'http://localhost:8000';
}

export const API_BASE = resolveBase();
export const WS_BASE = API_BASE.replace(/^http/, 'ws');

/** Optional shared secret — must match DEADSAT_API_KEY on Pi #1. */
const API_KEY = ((import.meta as any).env?.VITE_API_KEY as string | undefined) ?? '';

function headers(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  if (API_KEY) h['X-API-Key'] = API_KEY;
  return h;
}

// ──────────────────────────────────────────────────────────────────────
// Backend payload shapes
// ──────────────────────────────────────────────────────────────────────

/** One telemetry frame as emitted by satellite_emulator._build_frame(). */
export interface TelemetryFrame {
  timestamp: number;
  frame_id: number;
  norad_id: number;

  obc_register: string;
  obc_temp_c: number;
  obc_error_count: number;
  obc_cpu_pct: number;
  obc_memory_pct: number;
  obc_status: string;

  adcs_rate_deg_s: number;
  adcs_quaternion: number[];
  adcs_wheel_rpm: number;
  adcs_pointing_err_deg: number;
  adcs_status: string;

  power_w: number;
  battery_pct: number;
  bus_voltage_v: number;
  power_charging: boolean;
  power_status: string;

  comms_uplink: boolean;
  comms_downlink: boolean;
  signal_strength_dbm: number;
  comms_status: string;

  fault_injected: string | null;
  fault_detail: Record<string, unknown>;

  overall_health?: string;
}

export type BackendFaultType =
  | 'SEU'
  | 'software_bug'
  | 'firmware_corruption'
  | 'command_injection';

export interface AgentEvent {
  event: string;
  payload: Record<string, any>;
  timestamp: string;
}

export interface LinkStatus {
  all_connected: boolean;
  links: Record<string, { connected: boolean; detail: string }>;
}

// ──────────────────────────────────────────────────────────────────────
// REST helpers
// ──────────────────────────────────────────────────────────────────────

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: headers(), ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json())?.detail ?? detail;
    } catch {
      /* response had no JSON body */
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  /** Latest telemetry frame (poll fallback when the WebSocket is down). */
  telemetry: () => req<TelemetryFrame>('/telemetry'),

  /** Ring-buffer history — the same window AI-1 classifies over. */
  history: (n = 60) => req<any>(`/telemetry/history?n=${n}`),

  health: () => req<any>('/health'),

  /** Ground-contact window from the SGP4 calculator. */
  contact: () => req<any>('/contact'),

  /** Real GP orbital elements + generated TLE for one satellite. */
  satellite: (noradId: number) => req<any>(`/catalog/satellite/${noradId}`),

  /** Catalog summary (712 satellites loaded from the CSV datasets). */
  catalogStats: () => req<any>('/catalog/stats'),

  /** Live status of every inter-component link, including Pi #2. */
  links: () => req<LinkStatus>('/system/links'),

  /** Non-secret view of the active deployment wiring. */
  config: () => req<any>('/system/config'),

  /** RF ground station on Pi #2, proxied through Pi #1. */
  rfStatus: () => req<any>('/rf/status'),

  /** Live RTL-SDR power spectrum from Pi #2, proxied through Pi #1. */
  rfSpectrum: () => req<any>('/rf/spectrum'),

  /** Inject a fault into the emulator. */
  injectFault: (faultType: BackendFaultType, register = '0x3F') =>
    req<any>('/fault/inject', {
      method: 'POST',
      body: JSON.stringify({ fault_type: faultType, register }),
    }),

  /** Hand a fault report to the AI-2 recovery agent. */
  triggerRecovery: (faultType: BackendFaultType, faultDetail: object = {}) =>
    req<any>('/recovery/trigger', {
      method: 'POST',
      body: JSON.stringify({ fault_type: faultType, fault_detail: faultDetail }),
    }),

  /** Full cycle: inject -> AI-1 classify -> AI-2 recover. */
  runPipeline: (
    faultType: BackendFaultType,
    opts: { skipClassifier?: boolean; noradId?: number } = {},
  ) =>
    req<any>('/pipeline/run', {
      method: 'POST',
      body: JSON.stringify({
        fault_type: faultType,
        skip_classifier: opts.skipClassifier ?? false,
        norad_id: opts.noradId ?? 28654,
      }),
    }),

  /** Are the AI-1 artifacts trained and loadable? */
  pipelineStatus: () => req<any>('/pipeline/status'),

  /** Run AI-1 over the current emulator window and return the fault report. */
  classify: (noradId = 28654) =>
    req<any>('/pipeline/classify', {
      method: 'POST',
      body: JSON.stringify({ norad_id: noradId }),
    }),

  /** Crypto ledger + rogue-command alerts. */
  cryptoStatus: () => req<any>('/crypto/status'),
  cryptoLedger: () => req<any>('/crypto/ledger'),
  cryptoAlerts: () => req<any>('/crypto/alerts'),

  /** Ask CY-1 to rotate the signing keypair. */
  cryptoRotate: () => req<any>('/crypto/rotate', { method: 'POST' }),

  reset: () => req<any>('/reset', { method: 'POST' }),
};

// ──────────────────────────────────────────────────────────────────────
// WebSocket helper — auto-reconnecting
// ──────────────────────────────────────────────────────────────────────

export interface SocketHandle {
  close: () => void;
}

/**
 * Subscribe to a WebSocket channel with exponential-backoff reconnect.
 * Returns a handle whose `close()` stops reconnecting.
 */
function subscribe<T>(
  path: string,
  onMessage: (data: T) => void,
  onStatus?: (connected: boolean) => void,
): SocketHandle {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const connect = () => {
    if (closed) return;
    try {
      // The WebSocket endpoints now enforce DEADSAT_API_KEY (they previously
      // accepted any connection, so live telemetry was readable by anyone on
      // the LAN even with the key set). A browser cannot set headers on a
      // WebSocket handshake, so the key goes in the query string.
      const auth = API_KEY ? `?api_key=${encodeURIComponent(API_KEY)}` : '';
      ws = new WebSocket(`${WS_BASE}${path}${auth}`);
    } catch {
      schedule();
      return;
    }

    ws.onopen = () => {
      retry = 0;
      onStatus?.(true);
    };

    ws.onmessage = (ev) => {
      try {
        onMessage(JSON.parse(ev.data) as T);
      } catch {
        /* ignore malformed frame */
      }
    };

    ws.onerror = () => ws?.close();

    ws.onclose = () => {
      onStatus?.(false);
      schedule();
    };
  };

  const schedule = () => {
    if (closed) return;
    const delay = Math.min(1000 * 2 ** retry, 15000); // 1s -> 15s ceiling
    retry += 1;
    timer = setTimeout(connect, delay);
  };

  connect();

  return {
    close: () => {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    },
  };
}

/** Live telemetry stream — one frame per second from the emulator. */
export const subscribeTelemetry = (
  onFrame: (f: TelemetryFrame) => void,
  onStatus?: (c: boolean) => void,
) => subscribe<TelemetryFrame>('/ws/telemetry', onFrame, onStatus);

/** Agent/recovery/pipeline event stream. */
export const subscribeEvents = (
  onEvent: (e: AgentEvent) => void,
  onStatus?: (c: boolean) => void,
) => subscribe<AgentEvent>('/ws/events', onEvent, onStatus);

// ──────────────────────────────────────────────────────────────────────
// Mapping: backend frame -> the UI's existing TelemetryState shape
// ──────────────────────────────────────────────────────────────────────

/**
 * The dashboard was built around a display model (pitch/yaw, bandwidth,
 * altitude, lat/lng) that predates the backend's subsystem telemetry. Rather
 * than rewrite every component, map the real frame onto that shape.
 *
 * `orbit` supplies the values the emulator does not model — altitude,
 * velocity and ground track come from the catalog/contact calculator, so the
 * caller passes whatever it has and we fall back to sensible LEO constants.
 */
export function frameToTelemetryState(
  f: TelemetryFrame,
  prev?: { lat: number; lng: number; altitude: number; velocity: number },
) {
  const stability: 'NOMINAL' | 'WARN' | 'CRITICAL' =
    f.adcs_status === 'fault'
      ? 'CRITICAL'
      : f.adcs_status === 'degraded' || f.adcs_rate_deg_s > 0.05
        ? 'WARN'
        : 'NOMINAL';

  // ⚠ NOT REAL TELEMETRY — see `simulatedFields` on the returned object.
  //
  // The emulator models subsystems, not orbital position, so there is no
  // ground track in the frame. These are a fixed increment per tick: they
  // move smoothly and look plausible, which is precisely the problem — the
  // globe presents them with the same authority as the measured values.
  //
  // The data to compute this properly already exists: satellite_catalog has
  // the GP elements and emulator/contact_calculator.py runs SGP4. The honest
  // fix is to expose sub-satellite lat/lon from the propagator and use it.
  // Until then this is labelled rather than dressed up.
  const lat = prev ? Number(((prev.lat + 0.002) % 180).toFixed(4)) : 32.51;
  const lng = prev ? Number(((prev.lng + 0.005) % 360).toFixed(4)) : 122.36;

  return {
    powerArray: Number(f.power_w?.toFixed(2) ?? 0),
    adcsPitch: Number((f.adcs_pointing_err_deg ?? 0).toFixed(2)),
    adcsYaw: Number((f.adcs_rate_deg_s ?? 0).toFixed(2)),
    adcsStability: stability,
    // Downlink off => no bandwidth; scale signal strength into a Gbps-ish figure.
    commsBandwidth: f.comms_downlink
      ? Number((Math.max(0, (f.signal_strength_dbm + 100) / 30)).toFixed(2))
      : 0,
    obcCpu: Math.round(f.obc_cpu_pct ?? 0),
    obcMem: Math.round(f.obc_memory_pct ?? 0),
    altitude: prev?.altitude ?? 402.18,
    velocity: prev?.velocity ?? 7.672,
    lat,
    lng,
    temperature: Number(((f.obc_temp_c ?? 20) + 273.15).toFixed(2)),
  };
}

/**
 * Fields of TelemetryState that are NOT derived from the telemetry frame.
 *
 * Everything else returned by frameToTelemetryState() comes from the emulator.
 * These four are placeholders: the emulator models subsystems, not orbital
 * position, so there is no ground track, altitude or velocity in the frame.
 *
 * Exported separately (rather than as a property on the returned object) so it
 * carries no type-level consequences for TelemetryState. A component that
 * displays any of these should mark them as illustrative — presenting invented
 * numbers with the same authority as measured ones is the specific problem
 * this list exists to prevent.
 */
export const SIMULATED_TELEMETRY_FIELDS = ['lat', 'lng', 'altitude', 'velocity'] as const;

/** UI fault ids -> backend fault types (the UI offers 5, the backend has 4). */
export const UI_FAULT_TO_BACKEND: Record<string, BackendFaultType> = {
  seu: 'SEU',
  leak: 'software_bug',
  injection: 'command_injection',
  // The emulator has no dedicated battery fault; firmware_corruption is the
  // one that degrades the power bus, so it is the closest available analogue.
  battery_fail: 'firmware_corruption',
  // An ADCS actuator failure presents like an SEU in this emulator.
  adcs_fail: 'SEU',
};
