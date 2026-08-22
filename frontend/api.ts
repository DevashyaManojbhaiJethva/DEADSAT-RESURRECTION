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

/** JWT lives only in module memory for the active browser session. */
let sessionToken = '';

export function setSessionToken(token: string | null): void {
  sessionToken = token ?? '';
}

function headers(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  if (sessionToken) h.Authorization = `Bearer ${sessionToken}`;
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
  /** Server-side privacy decision for a historical frame, when requested. */
  access?: 'FULL' | 'LIMITED' | 'SUMMARY' | 'REDACTED';

  overall_health?: string;
}

export type BackendFaultType =
  | 'SEU'
  | 'software_bug'
  | 'firmware_corruption'
  | 'command_injection'
  | 'battery_failure'
  | 'adcs_failure';

/**
 * Faults AI-1 cannot classify — it reads orbital elements only, and neither
 * battery state nor reaction-wheel health leaves a signature in a TLE.
 * /pipeline/run forces skip_classifier for these, so the reported diagnosis is
 * the operator's own selection rather than a guess from the wrong four classes.
 */
export const CLASSIFIER_BLIND_FAULTS: readonly BackendFaultType[] = [
  'battery_failure',
  'adcs_failure',
] as const;

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

export interface LoginResponse {
  access_token: string;
  token_type: 'bearer';
}

/** Authenticate against the backend; credentials and signing never leave it. */
export async function login(username: string, password: string): Promise<LoginResponse> {
  const result = await req<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  if (result.token_type !== 'bearer' || !result.access_token) throw new Error('Invalid login response');
  setSessionToken(result.access_token);
  return result;
}

async function websocketToken(): Promise<string> {
  const result = await req<{ connection_token: string; token_type: string }>('/auth/ws-token', { method: 'POST' });
  if (result.token_type !== 'websocket' || !result.connection_token) throw new Error('Invalid WebSocket token response');
  return result.connection_token;
}

export const api = {
  /** Latest telemetry frame (poll fallback when the WebSocket is down). */
  telemetry: () => req<TelemetryFrame>('/telemetry'),

  /** Ring-buffer history — the same window AI-1 classifies over. */
  history: (n = 60, intent = 'monitoring') =>
    req<any>(`/telemetry/history?n=${n}&intent=${encodeURIComponent(intent)}`),

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

  /** RF intelligence analysis from the RF pipeline. */
  rfIntelligence: () => req<any>('/rf/intelligence'),

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
 * One shared connection per path, fanned out to every subscriber.
 *
 * The dashboard opened SIX WebSockets where two would do — three to
 * /ws/telemetry (useDeadsat, AiDiagnostics, SatelliteDashboard) and three to
 * /ws/events (useDeadsat, OperatorControlPanel, SatelliteDashboard). Every
 * frame was serialised by the server and parsed by the browser three times
 * over, which matters on a Raspberry Pi 4 pushing a frame a second.
 *
 * Multiplexing here rather than in a React context keeps subscribeTelemetry()
 * and subscribeEvents() signature-compatible, so all four components are
 * untouched. The reconnect logic below is the original, verbatim.
 */
interface Channel {
  ws: WebSocket | null;
  listeners: Set<(data: any) => void>;
  statusListeners: Set<(connected: boolean) => void>;
  connected: boolean;
  /**
   * Last `{type:'history'}` envelope seen on this channel.
   *
   * Without this, sharing the socket would break the Prompt 6.0 backfill for
   * any component that mounts AFTER the connection opened — switching to the
   * dashboard tab, for instance. The envelope arrives once per connect, so a
   * late subscriber would never see it and its chart would start empty.
   * Replayed to each new listener instead.
   */
  lastHistory: any | null;
  retry: number;
  timer: ReturnType<typeof setTimeout> | null;
  closed: boolean;
}

const channels = new Map<string, Channel>();

function openChannel(path: string): Channel {
  const ch: Channel = {
    ws: null,
    listeners: new Set(),
    statusListeners: new Set(),
    connected: false,
    lastHistory: null,
    retry: 0,
    timer: null,
    closed: false,
  };

  const setStatus = (up: boolean) => {
    ch.connected = up;
    ch.statusListeners.forEach((fn) => fn(up));
  };

  const connect = async () => {
    if (ch.closed) return;
    try {
      // Browsers cannot set handshake headers. Exchange the bearer session for
      // a short-lived, websocket-only token rather than exposing the access JWT.
      const token = await websocketToken();
      if (ch.closed) return;
      ch.ws = new WebSocket(`${WS_BASE}${path}?connection_token=${encodeURIComponent(token)}&intent=monitoring`);
    } catch {
      schedule();
      return;
    }

    ch.ws.onopen = () => {
      ch.retry = 0;
      setStatus(true);
    };

    ch.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data?.type === 'history') ch.lastHistory = data;
        ch.listeners.forEach((fn) => fn(data));
      } catch {
        /* ignore malformed frame */
      }
    };

    ch.ws.onerror = () => ch.ws?.close();

    ch.ws.onclose = () => {
      // A fresh connection sends a fresh backfill; do not replay a stale one.
      ch.lastHistory = null;
      setStatus(false);
      schedule();
    };
  };

  const schedule = () => {
    if (ch.closed) return;
    const delay = Math.min(1000 * 2 ** ch.retry, 15000); // 1s -> 15s ceiling
    ch.retry += 1;
    ch.timer = setTimeout(connect, delay);
  };

  connect();
  return ch;
}

/**
 * Subscribe to a WebSocket channel with exponential-backoff reconnect.
 * Returns a handle whose `close()` detaches this subscriber; the underlying
 * socket is torn down once the last subscriber has gone.
 */
function subscribe<T>(
  path: string,
  onMessage: (data: T) => void,
  onStatus?: (connected: boolean) => void,
): SocketHandle {
  let ch = channels.get(path);
  if (!ch) {
    ch = openChannel(path);
    channels.set(path, ch);
  }

  const listener = (data: any) => onMessage(data as T);
  ch.listeners.add(listener);
  if (onStatus) ch.statusListeners.add(onStatus);

  // Bring the new subscriber up to date with a connection that is already
  // open: current status, then the backfill it would otherwise have missed.
  if (ch.connected) {
    onStatus?.(true);
    if (ch.lastHistory) {
      const replay = ch.lastHistory;
      queueMicrotask(() => listener(replay));
    }
  }

  let detached = false;
  return {
    close: () => {
      if (detached) return;          // close() must be idempotent
      detached = true;
      const channel = channels.get(path);
      if (!channel) return;
      channel.listeners.delete(listener);
      if (onStatus) channel.statusListeners.delete(onStatus);

      if (channel.listeners.size === 0 && channel.statusListeners.size === 0) {
        channel.closed = true;
        if (channel.timer) clearTimeout(channel.timer);
        channel.ws?.close();
        channels.delete(path);
      }
    },
  };
}

/** The backfill envelope /ws/telemetry sends as its FIRST message on connect. */
export interface TelemetryHistoryMessage {
  type: 'history';
  frames: TelemetryFrame[];
  count: number;
}

/**
 * Live telemetry stream — one frame per second from the emulator.
 *
 * The first message on this socket is NOT a frame. main.py sends
 *
 *     {"type": "history", "frames": [...up to 60...], "count": 60}
 *
 * so charts can fill instantly on connect. Nothing checked `type`, so the
 * envelope was handed to the frame handler on every connect AND every
 * reconnect. With no telemetry fields on it, consumers rendered
 * `SP: 0x1FFF00NaN` (Math.round(undefined)), pushed a zeroed point onto the
 * chart, logged "WS frame undefined", and flashed all five diagnostic
 * channels red CRITICAL for about a second. The 60 backfilled frames were
 * then discarded — the feature the envelope exists for had never worked.
 *
 * Branching here rather than inside subscribe() fixes all three callers at
 * once (useDeadsat, AiDiagnostics, SatelliteDashboard) and leaves the generic
 * helper and its reconnect logic untouched. `onHistory` is optional, so a
 * caller that does not want the backfill simply no longer receives the
 * envelope as a frame.
 */
export const subscribeTelemetry = (
  onFrame: (f: TelemetryFrame) => void,
  onStatus?: (c: boolean) => void,
  onHistory?: (frames: TelemetryFrame[]) => void,
) =>
  subscribe<TelemetryFrame>(
    '/ws/telemetry',
    (msg) => {
      const envelope = msg as unknown as Partial<TelemetryHistoryMessage>;
      if (envelope?.type === 'history') {
        onHistory?.(envelope.frames ?? []);
        return;
      }
      onFrame(msg);
    },
    onStatus,
  );

/** Agent/recovery/pipeline event stream. */
export const subscribeEvents = (
  onEvent: (e: AgentEvent) => void,
  onStatus?: (c: boolean) => void,
) => subscribe<AgentEvent>('/ws/events', onEvent, onStatus);

/** RF frame interface from the RF models. */
export interface RFFrame {
  timestamp: string;
  sequence: number;
  source_node: string;
  schema_version: string;
  frequency_hz: number;
  sample_rate: number;
  gain: number;
  signal_dbm: number;
  snr_db: number;
  noise_floor_dbm: number;
  doppler_hz: number;
  satellite_velocity_ms?: number;
  spectrum: number[];
  spectrum_freqs: number[];
  norad_id?: number;
  satellite_name?: string;
  elevation_deg: number;
  azimuth_deg: number;
  range_km: number;
  rf_health: string;
  mode: string;
  frame_quality: number;
  receiving: boolean;
}

/** RF data stream — live RF frames from Pi #2. */
export const subscribeRF = (
  onFrame: (f: RFFrame) => void,
  onStatus?: (c: boolean) => void,
) => subscribe<RFFrame>('/ws/rf', onFrame, onStatus);

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
  // FIXED: these two used to be lies. battery_fail mapped to
  // firmware_corruption and adcs_fail to SEU, "the closest available
  // analogue" — so selecting either produced a diagnosis and a recovery
  // procedure contradicting the label the operator had just picked. The
  // emulator now models both as first-class faults with their own injectors,
  // per-tick effects and procedures in procedure_library.json.
  battery_fail: 'battery_failure',
  adcs_fail: 'adcs_failure',
};
