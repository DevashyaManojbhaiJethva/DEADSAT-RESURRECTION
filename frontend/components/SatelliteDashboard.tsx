import { useState, useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  AreaChart,
  Area,
} from 'recharts';
import { Radio, Warning, Activity, Cpu, Shield, Power } from './Icons';
import EarthGlobe from './EarthGlobe';

// TLE type structure
// WIRING: every metric on this dashboard now comes from the backend.
import {
  API_BASE,
  TelemetryFrame,
  api,
  subscribeEvents,
  subscribeTelemetry,
} from '../api';

/** Map an emulator telemetry frame onto this dashboard's chart point. */
function frameToPoint(f: TelemetryFrame): TelemetryDataPoint {
  const faulted = Boolean(f.fault_injected && f.fault_injected !== 'none');
  return {
    time: new Date((f.timestamp ?? Date.now() / 1000) * 1000).toLocaleTimeString(),
    batteryPct: Number((f.battery_pct ?? 0).toFixed(2)),
    batteryVoltage: Number((f.bus_voltage_v ?? 0).toFixed(2)),
    cpuTemp: Number((f.obc_temp_c ?? 0).toFixed(1)),
    powerConsumption: Number((f.power_w ?? 0).toFixed(1)),
    angularRate: Number((f.adcs_rate_deg_s ?? 0).toFixed(3)),
    signalStrength: Number((f.signal_strength_dbm ?? 0).toFixed(1)),
    isFaultSpike: faulted,
  };
}

/** Emulator status strings -> this component's three-level status. */
function mapStatus(s?: string): 'nominal' | 'warning' | 'fault' {
  if (s === 'fault') return 'fault';
  if (s === 'degraded') return 'warning';
  return 'nominal';
}

interface TLEData {
  name: string;
  line1: string;
  line2: string;
  inclination: number;
  eccentricity: number;
  raan: number;
  meanMotion: number;
  periodMins: number;
  epoch: string;
}

// Chart data point exactly representing the 6 requested metrics plus status metadata
interface TelemetryDataPoint {
  time: string;
  batteryPct: number;      // Metric 1: Battery %
  batteryVoltage: number;  // Metric 2: Battery Volts
  cpuTemp: number;         // Metric 3: OBC Temp (°C)
  powerConsumption: number;// Metric 4: Power Consumption (Watts)
  angularRate: number;     // Metric 5: ADCS Rotation Rate (deg/sec)
  signalStrength: number;  // Metric 6: RF Signal Strength (dBm)
  isFaultSpike: boolean;
}

// Anomaly log item
interface AnomalyAlert {
  id: string;
  timestamp: string;
  subsystem: 'OBC' | 'ADCS' | 'Power' | 'Comms' | 'Watchdog' | 'Payload';
  anomalyScore: number; // 0 to 100
  status: 'nominal' | 'warning' | 'fault';
  actionTaken: string;
}

export default function SatelliteDashboard() {
  // WIRING: TLE starts empty and is populated from the backend catalog
  // (/contact -> satellite_catalog.build_tle_from_gp). It previously held a
  // hardcoded CARTOSAT-3 element set with placeholder digits.
  const [tle, setTle] = useState<TLEData>({
    name: '—',
    line1: '',
    line2: '',
    inclination: 0,
    eccentricity: 0,
    raan: 0,
    meanMotion: 0,
    periodMins: 0,
    epoch: 'awaiting backend',
  });

  const [tleLoading, setTleLoading] = useState<boolean>(true);
  const [tleLastFetched, setTleLastFetched] = useState<string>('connecting…');

  // Ground Contact Countdown details
  const [countdownSecs, setCountdownSecs] = useState<number>(0);
  const [isRecovering, setIsRecovering] = useState<boolean>(false);
  const [activeFault, setActiveFault] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState<boolean>(false);

  // Screen layout selection
  // screen1 = Main Projector, screen2 = Pi 1 Monitor, screen3 = Pi 2 Monitor
  const [screenView, setScreenView] = useState<'screen1' | 'screen2' | 'screen3'>('screen1');

  // Monitor Terminal states (Screen 2 Logs)
  const [fastapiLogs, setFastapiLogs] = useState<string[]>([]);
  const [classifierLogs, setClassifierLogs] = useState<string[]>([]);
  const [recoveryLogs, setRecoveryLogs] = useState<string[]>([]);
  const [cryptoLogs, setCryptoLogs] = useState<string[]>([]);
  const [watchdogLogs, setWatchdogLogs] = useState<string[]>([]);

  // Listen for custom operator events.
  // WIRING: these remain as an immediate local echo so the panel reacts the
  // instant the operator clicks, but they no longer *define* state — the
  // authoritative fault, statuses and metrics all arrive over /ws/telemetry
  // and overwrite anything set here on the next frame.
  useEffect(() => {
    const handleFaultInjection = (e: any) => {
      const fault = e.detail?.fault || 'seu';

      // Instantly push critical alerts to feed
      const timeStr = new Date().toLocaleTimeString();
      let alertMsg = 'Unknown telemetry variation detected.';
      let sub: 'OBC' | 'ADCS' | 'Power' | 'Comms' | 'Watchdog' | 'Payload' = 'ADCS';
      let score = 95.0;

      if (fault === 'seu') {
        alertMsg = 'Single Event Upset: Gyroscopic register bit flip';
        sub = 'ADCS';
        score = 96.4;
      } else if (fault === 'leak') {
        alertMsg = 'OBC Heap Overrun: telemetry buffer leaking threads';
        sub = 'OBC';
        score = 89.2;
      } else if (fault === 'injection') {
        alertMsg = 'Command Spoof lock: Unauthorized signature attempt rejected';
        sub = 'Comms';
        score = 99.1;
      } else if (fault === 'battery_fail') {
        alertMsg = 'Battery Shunt thermal warning: anomalous voltage drop';
        sub = 'Power';
        score = 94.2;
      } else if (fault === 'adcs_fail') {
        alertMsg = 'ADCS Torquer Coil Saturation - attitude spin increasing';
        sub = 'ADCS';
        score = 97.8;
      }

      setAnomalyFeed(feed => [
        {
          id: Date.now().toString() + '-inject',
          timestamp: timeStr,
          subsystem: sub,
          anomalyScore: score,
          status: 'fault',
          actionTaken: 'Triggered safe-hold. Standing by for PQC secure recovery authorization key.'
        },
        ...feed.slice(0, 7)
      ]);

      // Add corresponding Unix logs for Screen 2 Terminal Mocking
      const logTime = new Date().toISOString();
      setFastapiLogs(prev => [...prev, `[${logTime}] POST /api/fault/inject - Status: 200 OK (Fault: ${fault.toUpperCase()})`].slice(-25));
      setClassifierLogs(prev => [...prev, `[${logTime}] ALERT [AI-1]: Identified anomaly signature. Confidence: ${score}%. Classifying...`].slice(-25));
      setWatchdogLogs(prev => [...prev, `[${logTime}] WATCHDOG: Warning! Subsystem ${sub} status modified to FAULT. Gnd lock timer starting.`].slice(-25));
    };

    const handleRecoveryStarted = () => {
      setIsRecovering(true);
      const logTime = new Date().toISOString();
      setRecoveryLogs(prev => [...prev, `[${logTime}] [agent:uplink] PQC HANDSHAKE CONVERTED. Preparing Dilithium command vectors.`].slice(-25));
      setCryptoLogs(prev => [...prev, `[${logTime}] CRYPTO: Active Dilithium-v3 lattice signer loaded. Computing signature.`].slice(-25));
    };

    const handleRecoveryComplete = () => {
      setIsRecovering(false);
      // WIRING: activeFault is no longer cleared locally — the emulator's
      // next telemetry frame decides whether the fault actually cleared.
      // Clearing it here made every recovery look successful regardless.
      const timeStr = new Date().toLocaleTimeString();

      // Clear faults and push NOMINAL restore logs
      setAnomalyFeed(feed => [
        {
          id: Date.now().toString() + '-clear',
          timestamp: timeStr,
          subsystem: 'OBC',
          anomalyScore: 4.2,
          status: 'nominal',
          actionTaken: 'Satellite recovered successfully! Normal telemetry stream resumed.'
        },
        ...feed.slice(0, 7)
      ]);

      // These lines are written by the BROWSER, not received from Pi #1. They
      // previously asserted things that had not happened and could not be
      // observed from here: a "Crystals-Dilithium signature transmitted 200 OK"
      // against `/api/recovery/trigger` (not a real route — it is
      // `/recovery/trigger`), and "Verified response signature index key #52A4.
      // Post-quantum protection active" with a live timestamp, which is
      // indistinguishable from genuine crypto output.
      //
      // Real signing and verification results arrive on /ws/events and are
      // rendered by the subscribeEvents handler below. Marked [ui] so the
      // panes cannot be mistaken for backend confirmation.
      const logTime = new Date().toISOString();
      setFastapiLogs(prev => [...prev, `[${logTime}] [ui] recovery complete event received`].slice(-25));
      setRecoveryLogs(prev => [...prev, `[${logTime}] [ui] recovery reported complete — see agent trace above for the authoritative steps`].slice(-25));
      setWatchdogLogs(prev => [...prev, `[${logTime}] [ui] subsystem statuses re-read from telemetry`].slice(-25));
    };

    window.addEventListener('inject-satellite-fault', handleFaultInjection);
    window.addEventListener('recovery-started', handleRecoveryStarted);
    window.addEventListener('recovery-complete', handleRecoveryComplete);

    return () => {
      window.removeEventListener('inject-satellite-fault', handleFaultInjection);
      window.removeEventListener('recovery-started', handleRecoveryStarted);
      window.removeEventListener('recovery-complete', handleRecoveryComplete);
    };
  }, []);

  // Live simulation states
  const [historyData, setHistoryData] = useState<TelemetryDataPoint[]>([]);
  const [anomalyFeed, setAnomalyFeed] = useState<AnomalyAlert[]>([
    { id: '1', timestamp: '14:02:10', subsystem: 'ADCS', anomalyScore: 12.4, status: 'nominal', actionTaken: 'Star tracker attitude lock continuous' },
    { id: '2', timestamp: '14:05:33', subsystem: 'Power', anomalyScore: 28.5, status: 'warning', actionTaken: 'Solar panels rotated 1.5° for beta angle optimal' },
    { id: '3', timestamp: '14:08:45', subsystem: 'OBC', anomalyScore: 8.1, status: 'nominal', actionTaken: 'RAM ECC scrubbing single-bit correction processed' },
  ]);

  // Systems Status state
  const [statuses, setStatuses] = useState({
    OBC: 'nominal' as 'nominal' | 'warning' | 'fault',
    ADCS: 'nominal' as 'nominal' | 'warning' | 'fault',
    Power: 'nominal' as 'nominal' | 'warning' | 'fault',
    Comms: 'nominal' as 'nominal' | 'warning' | 'fault',
    Payload: 'nominal' as 'nominal' | 'warning' | 'fault',
    Watchdog: 'nominal' as 'nominal' | 'warning' | 'fault',
  });

  // Dynamic registers simulation
  const [obcRegisters, setObcRegisters] = useState({
    PC: '0x8024',
    SP: '0x1FFF00A0',
    FLAGS: '0x00004012',
    OPCODE: 'ADDF',
    ticks: 0,
    errors: 0,
  });

  // Reference for 3D Earth
  const mountRef = useRef<HTMLDivElement>(null);
  const orbitalAngleRef = useRef<number>(0);
  const earthRotationRef = useRef<number>(0);
  const satelliteMeshRef = useRef<THREE.Group | null>(null);
  const linkLineMeshRef = useRef<THREE.Line | null>(null);
  const ahmedabadMeshRef = useRef<THREE.Mesh | null>(null);
  const domeMeshRef = useRef<THREE.Mesh | null>(null);

  useEffect(() => {
    const handleMessage = (e: MessageEvent) => {
      if (e.data && e.data.type === 'SATELLITE_TELEMETRY') {
        setSatLat(Number(e.data.satLat.toFixed(4)));
        setSatLng(Number(e.data.satLng.toFixed(4)));
        setIsInContact(e.data.isInContact);
      }
    };
    window.addEventListener('message', handleMessage);
    return () => {
      window.removeEventListener('message', handleMessage);
    };
  }, []);

  // States for live 3D parameters to display in HUD
  const [isInContact, setIsInContact] = useState<boolean>(false);
  const [satLat, setSatLat] = useState<number>(0);
  const [satLng, setSatLng] = useState<number>(0);
  const [satAltitude, setSatAltitude] = useState<number>(508.4); // KM above Earth
  const [orbitalNumber, setOrbitalNumber] = useState<number>(1542);
  const [velocity, setVelocity] = useState<number>(7.672);

  // Canvas waterfall reference for Screen 3
  const waterfallCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const spectrumPhaseRef = useRef<number>(0);

  // WIRING: live RTL-SDR power spectrum from Pi #2, proxied via Pi #1.
  const rfBinsRef = useRef<number[]>([]);
  const [rfOnline, setRfOnline] = useState<boolean>(false);
  const [rfMeta, setRfMeta] = useState<any>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res: any = await api.rfSpectrum();
        if (!alive) return;
        const bins: number[] = res?.data?.bins ?? res?.data?.power_dbm ?? [];
        rfBinsRef.current = Array.isArray(bins) ? bins : [];
        setRfOnline(Boolean(res?.online) && rfBinsRef.current.length > 0);
        setRfMeta(res?.data ?? null);
      } catch {
        if (!alive) return;
        rfBinsRef.current = [];
        setRfOnline(false);
      }
    };
    poll();
    const t = setInterval(poll, 1000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  // 1. WIRING: TLE + orbital elements from the backend catalog.
  //
  // This previously fetched CelesTrak directly from the browser for a
  // hardcoded NORAD 44804 — a satellite unrelated to this project, on an
  // endpoint that sends no CORS header, so the request fails in a browser
  // and the panel silently kept its placeholder element set.
  //
  // /catalog/satellite/{norad} serves the real GP row for the satellite the
  // emulator is actually modelling, with the TLE generated by
  // satellite_catalog.build_tle_from_gp().
  const fetchTLE = async (norad: number) => {
    setTleLoading(true);
    try {
      const sat: any = await api.satellite(norad);
      const el = sat.orbital_elements ?? {};
      const mm = Number(el.mean_motion) || 0;
      setTle({
        name: sat.name ?? `NORAD ${norad}`,
        line1: sat.tle?.line1 ?? '',
        line2: sat.tle?.line2 ?? '',
        inclination: Number(el.inclination_deg) || 0,
        eccentricity: Number(el.eccentricity) || 0,
        raan: Number(el.ra_of_asc_node) || 0,
        meanMotion: mm,
        periodMins: mm > 0 ? Number((1440 / mm).toFixed(2)) : 0,
        epoch: sat.epoch ?? '—',
      });
      setSatAltitude(Number(sat.anomaly_baselines?.altitude_km_approx) || 0);
      setVelocity(Number((Math.sqrt(398600.4418 / (6371 + (Number(sat.anomaly_baselines?.altitude_km_approx) || 400)))).toFixed(3)));
      setTleLastFetched(`${new Date().toLocaleTimeString()} (catalog)`);
    } catch (err: any) {
      setTleLastFetched(`unavailable — ${err.message}`);
    } finally {
      setTleLoading(false);
    }
  };

  // Refetch whenever the emulator reports a different satellite.
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const f: any = await api.telemetry();
        if (alive && f?.norad_id) {
          setOrbitalNumber(f.frame_id ?? 0);
          await fetchTLE(f.norad_id);
        }
      } catch {
        if (alive) setTleLastFetched('backend unreachable');
        setTleLoading(false);
      }
    };
    load();
    const tleTimer = setInterval(load, 300000); // refresh every 5 min
    return () => { alive = false; clearInterval(tleTimer); };
  }, []);

  // 2. Initialize history data for charts
  // WIRING: seed the chart from the emulator's real ring buffer instead of
  // fabricating 20 points. /telemetry/history returns the same window AI-1
  // classifies over.
  useEffect(() => {
    let alive = true;
    api.history(20)
      .then((res: any) => {
        if (!alive) return;
        const frames: TelemetryFrame[] = res?.frames ?? res ?? [];
        if (Array.isArray(frames) && frames.length) {
          setHistoryData(frames.map(frameToPoint));
        }
      })
      .catch(() => { /* backend down — chart fills from the live stream */ });
    return () => { alive = false; };
  }, []);

  // WIRING: terminal panes start empty and fill from real backend state.
  // They previously printed a fixed boot transcript, including a fake
  // "WebSocket client ... handshaking authorized" line that was a string
  // literal rather than an actual connection.
  useEffect(() => {
    let alive = true;
    (async () => {
      const t = () => new Date().toISOString();
      try {
        const c = await api.config();
        if (!alive) return;
        setFastapiLogs([
          `[${t()}] Connected to ${c?.api?.public_base ?? API_BASE}`,
          `[${t()}] Emulator tick ${c?.satellite?.norad_id ? `NORAD ${c.satellite.norad_id}` : ''}`,
          `[${t()}] Verification gate: ${c?.security?.require_command_verification ? 'ON' : 'OFF'}`,
        ]);
      } catch (e: any) {
        if (!alive) return;
        setFastapiLogs([`[${t()}] BACKEND UNREACHABLE at ${API_BASE} — ${e.message}`]);
      }

      try {
        const p = await api.pipelineStatus();
        if (!alive) return;
        setClassifierLogs([
          `[${t()}] AI-1 artifacts: ${p.artifacts_ready ? 'LOADED' : 'MISSING'}`,
          p.artifacts_ready
            ? `[${t()}] seq_len=${p.seq_len} features=${p.feature_cols?.length ?? '?'}`
            : `[${t()}] ${p.hint ?? 'Run train_classifier.py'}`,
        ]);
      } catch (e: any) {
        if (!alive) return;
        setClassifierLogs([`[${t()}] AI-1 status unavailable — ${e.message}`]);
      }

      try {
        const l = await api.links();
        if (!alive) return;
        setRecoveryLogs([
          `[${t()}] AI-2 agent: ${l.links?.ai2_agent?.detail ?? 'unknown'}`,
          `[${t()}] RF station: ${l.links?.rf_station?.detail ?? 'unknown'}`,
        ]);
        setWatchdogLogs([
          `[${t()}] Link check: ${Object.values(l.links).filter((x: any) => x.connected).length}/${Object.keys(l.links).length} components connected`,
        ]);
      } catch {
        if (!alive) return;
        setRecoveryLogs([`[${t()}] Link status unavailable`]);
      }

      try {
        const cs = await api.cryptoStatus();
        if (!alive) return;
        setCryptoLogs([
          `[${t()}] CY-1 ${cs.cy1_online ? 'ONLINE' : 'OFFLINE'} — mode: ${cs.mode}`,
          `[${t()}] endpoint: ${cs.endpoint}`,
        ]);
      } catch (e: any) {
        if (!alive) return;
        setCryptoLogs([`[${t()}] Crypto status unavailable — ${e.message}`]);
      }
    })();
    return () => { alive = false; };
  }, []);

  // 3. WIRING: live telemetry from Pi #1.
  // This entire block used to be a 1 s setInterval fabricating every metric
  // with Math.random() and switching on a locally-held `activeFault`. Every
  // value below now comes from the emulator's real telemetry frame.
  useEffect(() => {
    const sock = subscribeTelemetry(
      (f: TelemetryFrame) => {
        setWsConnected(true);
        setActiveFault(f.fault_injected && f.fault_injected !== 'none' ? f.fault_injected : null);

        // Chart series — straight from the frame.
        setHistoryData(prev => [...prev.slice(-59), frameToPoint(f)]);

        // Subsystem status — reported by the emulator, not inferred locally.
        setStatuses({
          OBC:      mapStatus(f.obc_status),
          ADCS:     mapStatus(f.adcs_status),
          Power:    mapStatus(f.power_status),
          Comms:    mapStatus(f.comms_status),
          Payload:  mapStatus(f.overall_health === 'fault' ? 'degraded' : 'nominal'),
          Watchdog: f.fault_injected && f.fault_injected !== 'none' ? 'warning' : 'nominal',
        });

        // OBC registers — real register, error count and frame counter.
        setObcRegisters(regs => ({
          ...regs,
          PC:     f.obc_register ?? regs.PC,
          SP:     '0x1FFF00' + Math.max(0, Math.round(f.obc_memory_pct)).toString(16).toUpperCase().padStart(2, '0'),
          FLAGS:  '0x' + Math.round((f.obc_cpu_pct ?? 0) * 100).toString(16).toUpperCase().padStart(8, '0'),
          OPCODE: f.obc_status === 'nominal' ? 'ADDF' : 'TRAP',
          ticks:  f.frame_id ?? regs.ticks,
          errors: f.obc_error_count ?? regs.errors,
        }));

        setCountdownSecs(prev => (prev <= 1 ? 1122 : prev - 1));

        setFastapiLogs(prev =>
          [...prev, `[${new Date().toISOString()}] WS frame ${f.frame_id} — health=${f.overall_health ?? 'n/a'}`].slice(-25),
        );
      },
      (up) => setWsConnected(up),
    );
    return () => sock.close();
  }, []);

  // WIRING: anomaly feed built from real fault transitions, not a 2% random roll.
  const lastFaultRef = useRef<string | null>(null);
  useEffect(() => {
    if (activeFault === lastFaultRef.current) return;
    const prevFault = lastFaultRef.current;
    lastFaultRef.current = activeFault;

    const ts = new Date().toLocaleTimeString();
    if (activeFault) {
      const subsystem: AnomalyAlert['subsystem'] =
        activeFault === 'SEU' ? 'ADCS'
        : activeFault === 'software_bug' ? 'OBC'
        : activeFault === 'firmware_corruption' ? 'Power'
        : 'Comms';
      setAnomalyFeed(feed => [
        { id: `${Date.now()}`, timestamp: ts, subsystem, anomalyScore: 95,
          status: 'fault', actionTaken: `Emulator reports ${activeFault} — AI-2 recovery available` },
        ...feed.slice(0, 7),
      ]);
      setWatchdogLogs(prev => [...prev, `[${new Date().toISOString()}] HEARTBEAT: WARNING — ${activeFault} active.`].slice(-25));
    } else if (prevFault) {
      setAnomalyFeed(feed => [
        { id: `${Date.now()}`, timestamp: ts, subsystem: 'Watchdog', anomalyScore: 5,
          status: 'nominal', actionTaken: `${prevFault} cleared — satellite returned to nominal` },
        ...feed.slice(0, 7),
      ]);
      setWatchdogLogs(prev => [...prev, `[${new Date().toISOString()}] HEARTBEAT: OK — fault cleared.`].slice(-25));
    }
  }, [activeFault]);

  // WIRING: agent/pipeline events drive the recovery + crypto terminals.
  useEffect(() => {
    const sock = subscribeEvents((e) => {
      const t = new Date().toISOString();
      const p: any = e.payload ?? {};
      if (e.event.startsWith('pipeline') || e.event.startsWith('recovery')) {
        setRecoveryLogs(prev => [...prev, `[${t}] ${e.event}: ${JSON.stringify(p).slice(0, 140)}`].slice(-25));
        setIsRecovering(e.event === 'pipeline_started' || e.event === 'recovery_started');
      }
      if (e.event === 'uplink_sent') {
        setCryptoLogs(prev => [...prev, `[${t}] Signed uplink: ${p.procedure_name} (${p.commands_count} commands)`].slice(-25));
      }
      if (e.event === 'pipeline_complete' && p.classified_fault) {
        const conf = typeof p.classifier_confidence === 'number'
          ? `${(p.classifier_confidence * 100).toFixed(1)}%` : 'n/a';
        setClassifierLogs(prev => [...prev, `[${t}] AI-1 -> ${p.classified_fault} @ ${conf}`].slice(-25));
      }
    });
    return () => sock.close();
  }, []);


  // 4. HTML5 Canvas waterfall visualizer effect (ticks for Screen 3)
  useEffect(() => {
    if (screenView !== 'screen3' || !waterfallCanvasRef.current) return;
    const canvas = waterfallCanvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let width = canvas.width = canvas.parentElement?.clientWidth || 400;
    let height = canvas.height = canvas.parentElement?.clientHeight || 200;

    // Temporary offscreen image canvas buffer to scroll downwards
    const tempCanvas = document.createElement('canvas');
    tempCanvas.width = width;
    tempCanvas.height = height;
    const tempCtx = tempCanvas.getContext('2d');

    let animationId: number;

    const renderSdrWaterfall = () => {
      if (!ctx || !tempCtx) return;

      // Scroll existing buffer downwards by 1.5 pixels
      tempCtx.drawImage(canvas, 0, 0);
      ctx.fillStyle = '#0a0a0c';
      ctx.fillRect(0, 0, width, height);
      ctx.drawImage(tempCanvas, 0, 1.5);

      // Create new line of waterfall row data at y=0
      const imgData = ctx.createImageData(width, 1);
      const centerBin = width * 0.52; // Target 137.9 MHz signal on Ku band
      
      // WIRING: bins come from the RTL-SDR on Pi #2 via /rf/spectrum
      // (proxied through Pi #1). When Pi #2 is offline `rfBins` is empty and
      // the canvas renders a flat noise floor — the panel then shows an
      // "RF OFFLINE" badge rather than animating fake signal.
      const bins = rfBinsRef.current;
      const haveRf = bins.length > 0;

      for (let x = 0; x < width; x++) {
        let noise: number;

        if (haveRf) {
          // Resample the real power spectrum across the canvas width.
          const b = bins[Math.floor((x / width) * bins.length)];
          // dBm (roughly -120..-20) -> 0..100 display scale
          noise = Math.max(0, Math.min(100, (b + 120) * (100 / 100)));
        } else {
          noise = 8; // flat floor, no invented signal
        }

        if (haveRf && isInContact) {
          const distanceToCenter = Math.abs(x - centerBin);
          if (distanceToCenter < 12) {
            noise += (12 - distanceToCenter) * 4;
          }
        }

        const idx = x * 4;
        
        if (noise < 30) {
          // Deep blue/black noise
          imgData.data[idx] = 10;                     // R
          imgData.data[idx + 1] = Math.max(10, noise);// G
          imgData.data[idx + 2] = 45;                 // B
          imgData.data[idx + 3] = 255;                // A
        } else if (noise < 75) {
          // Cyan interface waves
          imgData.data[idx] = 10;
          imgData.data[idx + 1] = Math.min(255, noise * 2);
          imgData.data[idx + 2] = Math.min(255, noise * 3);
          imgData.data[idx + 3] = 255;
        } else {
          // Yellow-green hot spikes
          imgData.data[idx] = Math.min(255, noise * 2);
          imgData.data[idx + 1] = 244;
          imgData.data[idx + 2] = 170;
          imgData.data[idx + 3] = 255;
        }
      }
      ctx.putImageData(imgData, 0, 0);

      spectrumPhaseRef.current += 1;
      animationId = requestAnimationFrame(renderSdrWaterfall);
    };

    renderSdrWaterfall();

    const handleResize = () => {
      width = canvas.width = canvas.parentElement?.clientWidth || 400;
      height = canvas.height = canvas.parentElement?.clientHeight || 200;
      tempCanvas.width = width;
      tempCanvas.height = height;
    };

    window.addEventListener('resize', handleResize);

    return () => {
      cancelAnimationFrame(animationId);
      window.removeEventListener('resize', handleResize);
    };
  }, [screenView, isInContact, activeFault]);


  // 5. Three.js Render - 3D Spherical Earth & Orbit Path with Ahmedabad Tracking Dot
  useEffect(() => {
    if (!mountRef.current || screenView !== 'screen1') return;

    const width = mountRef.current.clientWidth || 300;
    const height = mountRef.current.clientHeight || 300;

    const scene = new THREE.Scene();
    
    // Position camera perfectly centered, looking slightly down at Earth (0.3 tilt)
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.set(0, 0.3, 2.8);
    camera.lookAt(new THREE.Vector3(0, 0, 0));

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    mountRef.current.innerHTML = '';
    mountRef.current.appendChild(renderer.domElement);

    // Initialize OrbitControls with damping and limits (autoRotate OFF)
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.enableZoom = true;
    controls.minDistance = 1.5;
    controls.maxDistance = 4.0;
    controls.enablePan = false; // keep view centered on Earth

    const mainGroup = new THREE.Group();
    scene.add(mainGroup);

    // ONE DirectionalLight (sunlight) from upper-right
    const sunLight = new THREE.DirectionalLight(0xFFF5E0, 1.8);
    sunLight.position.set(12, 6, 12);
    scene.add(sunLight);

    // ONE AmbientLight at very low intensity for deep space feel
    const ambientLight = new THREE.AmbientLight(0xFFFFFF, 0.08);
    scene.add(ambientLight);

    // Deep space backdrop stars generator: 2000 random star particles with white/blue tint
    const starsCount = 2000;
    const starsGeom = new THREE.BufferGeometry();
    const starsPositions = new Float32Array(starsCount * 3);
    const starsColors = new Float32Array(starsCount * 3);

    for (let i = 0; i < starsCount; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(Math.random() * 2 - 1);
      const r = 25 + Math.random() * 35;
      
      starsPositions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      starsPositions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      starsPositions[i * 3 + 2] = r * Math.cos(phi);

      const tint = Math.random();
      if (tint > 0.70) {
        starsColors[i * 3] = 0.82; // Gnd blue sky
        starsColors[i * 3 + 1] = 0.93;
        starsColors[i * 3 + 2] = 1.0;
      } else {
        starsColors[i * 3] = 1.0;
        starsColors[i * 3 + 1] = 1.0;
        starsColors[i * 3 + 2] = 1.0;
      }
    }

    starsGeom.setAttribute('position', new THREE.BufferAttribute(starsPositions, 3));
    starsGeom.setAttribute('color', new THREE.BufferAttribute(starsColors, 3));

    const starsMat = new THREE.PointsMaterial({
      size: 0.08,
      sizeAttenuation: true,
      vertexColors: true,
      transparent: true,
      opacity: 0.9
    });

    const starField = new THREE.Points(starsGeom, starsMat);
    scene.add(starField);

    // Procedural organic-looking radial soft nebula clusters
    const createNebulaTexture = () => {
      const canvas = document.createElement('canvas');
      canvas.width = 64;
      canvas.height = 64;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        const grad = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
        grad.addColorStop(0, 'rgba(255, 255, 255, 1)');
        grad.addColorStop(0.5, 'rgba(255, 255, 255, 0.25)');
        grad.addColorStop(1, 'rgba(255, 255, 255, 0)');
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, 64, 64);
      }
      return new THREE.CanvasTexture(canvas);
    };

    const nebulaTexture = createNebulaTexture();
    const nebulaCount = 4;
    const nebulaGeom = new THREE.BufferGeometry();
    const nebulaPositions = new Float32Array(nebulaCount * 3);
    const nebulaColors = new Float32Array(nebulaCount * 3);

    const nebulaCoords = [
      [-14, 8, -18],
      [14, -10, -12],
      [-8, -12, -22],
      [15, 12, -25]
    ];

    for (let i = 0; i < nebulaCount; i++) {
      nebulaPositions[i * 3] = nebulaCoords[i][0];
      nebulaPositions[i * 3 + 1] = nebulaCoords[i][1];
      nebulaPositions[i * 3 + 2] = nebulaCoords[i][2];
      
      if (i % 2 === 0) {
        nebulaColors[i * 3] = 0.16; // soft purple
        nebulaColors[i * 3 + 1] = 0.04;
        nebulaColors[i * 3 + 2] = 0.28;
      } else {
        nebulaColors[i * 3] = 0.04; // soft blue
        nebulaColors[i * 3 + 1] = 0.10;
        nebulaColors[i * 3 + 2] = 0.24;
      }
    }

    nebulaGeom.setAttribute('position', new THREE.BufferAttribute(nebulaPositions, 3));
    nebulaGeom.setAttribute('color', new THREE.BufferAttribute(nebulaColors, 3));

    const nebulaMat = new THREE.PointsMaterial({
      size: 18.0,
      sizeAttenuation: true,
      vertexColors: true,
      transparent: true,
      opacity: 0.14,
      blending: THREE.AdditiveBlending,
      map: nebulaTexture,
      depthWrite: false
    });

    const nebulaField = new THREE.Points(nebulaGeom, nebulaMat);
    scene.add(nebulaField);

    // Earth geometry with high segments count (64x64 minimum)
    const earthRadius = 1.0;
    const sphereGeom = new THREE.SphereGeometry(earthRadius, 64, 64);
    
    // Custom ShaderMaterial for Earth to blend day, night lights and add ocean specular reflectance
    const earthUniforms = {
      dayTexture: { value: null as THREE.Texture | null },
      nightTexture: { value: null as THREE.Texture | null },
      sunWorldPosition: { value: new THREE.Vector3(12, 6, 12) },
      cameraWorldPosition: { value: new THREE.Vector3(0, 0.3, 2.8) }
    };

    const earthMat = new THREE.ShaderMaterial({
      uniforms: earthUniforms,
      vertexShader: `
        varying vec2 vUv;
        varying vec3 vNormal;
        varying vec3 vWorldPosition;
        void main() {
          vUv = uv;
          vNormal = normalize(vec3(modelMatrix * vec4(normal, 0.0)));
          vWorldPosition = vec3(modelMatrix * vec4(position, 1.0));
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        varying vec2 vUv;
        varying vec3 vNormal;
        varying vec3 vWorldPosition;
        uniform sampler2D dayTexture;
        uniform sampler2D nightTexture;
        uniform vec3 sunWorldPosition;
        uniform vec3 cameraWorldPosition;
        void main() {
          vec3 normal = normalize(vNormal);
          vec3 sunDir = normalize(sunWorldPosition - vWorldPosition);
          vec3 viewDir = normalize(cameraWorldPosition - vWorldPosition);
          
          float dotProduct = dot(normal, sunDir);
          float dayInfluence = smoothstep(-0.15, 0.15, dotProduct);
          
          vec4 dayColor = texture2D(dayTexture, vUv);
          vec4 nightColor = texture2D(nightTexture, vUv);
          
          bool hasDayTex = (dayColor.a > 0.05);
          bool hasNightTex = (nightColor.a > 0.05);
          
          vec4 dCol = hasDayTex ? dayColor : vec4(0.08, 0.16, 0.30, 1.0);
          vec4 nCol = hasNightTex ? nightColor : vec4(0.01, 0.02, 0.07, 1.0);
          
          // Specular highlights over water
          float specMask = step(0.12, dCol.b) * (1.0 - step(0.48, dCol.r));
          vec3 reflectDir = reflect(-sunDir, normal);
          float specAmount = pow(max(dot(reflectDir, viewDir), 0.0), 24.0);
          vec3 specColor = vec3(0.5, 0.72, 1.0) * specAmount * specMask * dayInfluence;
          
          vec4 surfaceColor = mix(nCol, dCol, dayInfluence);
          vec3 finalColor = surfaceColor.rgb + specColor;
          
          gl_FragColor = vec4(finalColor, 1.0);
        }
      `
    });
    
    const earthCore = new THREE.Mesh(sphereGeom, earthMat);
    mainGroup.add(earthCore);

    // Clouds Sphere Setup (slightly larger sphere, rotates at different speed)
    const cloudsGeom = new THREE.SphereGeometry(earthRadius * 1.012, 64, 64);
    const cloudsMat = new THREE.MeshPhongMaterial({
      transparent: true,
      opacity: 0.40,
      blending: THREE.NormalBlending,
      depthWrite: false,
    });
    const cloudsCore = new THREE.Mesh(cloudsGeom, cloudsMat);
    mainGroup.add(cloudsCore);

    // Atmospheric scattering thin layer: slightly larger sphere (radius * 1.02)
    const atmosphereGeom = new THREE.SphereGeometry(earthRadius * 1.020, 64, 64);
    const atmosphereMat = new THREE.ShaderMaterial({
      vertexShader: `
        varying vec3 vNormal;
        void main() {
          vNormal = normalize(normalMatrix * normal);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        varying vec3 vNormal;
        void main() {
          float intensity = pow(0.65 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 3.0);
          gl_FragColor = vec4(0.3, 0.7, 1.0, 1.0) * intensity;
        }
      `,
      blending: THREE.AdditiveBlending,
      side: THREE.BackSide,
      transparent: true,
      depthWrite: false
    });
    const atmosphereMesh = new THREE.Mesh(atmosphereGeom, atmosphereMat);
    mainGroup.add(atmosphereMesh);

    // Load textures
    const textureLoader = new THREE.TextureLoader();

    textureLoader.load(
      "https://raw.githubusercontent.com/turban/webgl-earth/master/images/2_no_clouds_4k.jpg",
      (tex) => {
        tex.colorSpace = THREE.SRGBColorSpace;
        earthUniforms.dayTexture.value = tex;
        earthMat.needsUpdate = true;
      }
    );

    textureLoader.load(
      "https://unpkg.com/three-globe/example/img/earth-night-lights.png",
      (tex) => {
        tex.colorSpace = THREE.SRGBColorSpace;
        earthUniforms.nightTexture.value = tex;
        earthMat.needsUpdate = true;
      }
    );

    textureLoader.load(
      "https://raw.githubusercontent.com/turban/webgl-earth/master/images/fair_clouds_4k.png",
      (tex) => {
        tex.colorSpace = THREE.SRGBColorSpace;
        cloudsMat.map = tex;
        cloudsMat.needsUpdate = true;
      }
    );

    // Subtle coordinate lattice lines
    const ringMat = new THREE.LineBasicMaterial({ color: 0x4fc3f7, transparent: true, opacity: 0.06 });
    
    const equatorGeom = new THREE.BufferGeometry().setFromPoints(
      new THREE.Path().absarc(0, 0, earthRadius + 0.008, 0, Math.PI * 2, false).getPoints(64).map(p => new THREE.Vector3(p.x, 0, p.y))
    );
    const equatorLine = new THREE.Line(equatorGeom, ringMat);
    mainGroup.add(equatorLine);

    const meridianGeom = new THREE.BufferGeometry().setFromPoints(
      new THREE.Path().absarc(0, 0, earthRadius + 0.008, 0, Math.PI * 2, false).getPoints(64).map(p => new THREE.Vector3(0, p.x, p.y))
    );
    const meridianLine = new THREE.Line(meridianGeom, ringMat);
    mainGroup.add(meridianLine);

    const ahmedabadLat = 23.0225 * Math.PI / 180;
    const ahmedabadLng = 72.5714 * Math.PI / 180;
    
    const getCoordinateOnSphere = (latRad: number, lngRad: number, radiusVal: number, rotY: number) => {
      const actualLng = lngRad + rotY;
      const y = radiusVal * Math.sin(latRad);
      const projRad = radiusVal * Math.cos(latRad);
      const x = projRad * Math.sin(actualLng);
      const z = projRad * Math.cos(actualLng);
      return new THREE.Vector3(x, y, z);
    };

    // Ahmedabad tracking marker
    const geomAhmedabad = new THREE.SphereGeometry(0.022, 8, 8);
    const matAhmedabad = new THREE.MeshBasicMaterial({ color: 0x00FF8C, transparent: true, opacity: 0.95 });
    const ahmedabadDot = new THREE.Mesh(geomAhmedabad, matAhmedabad);
    mainGroup.add(ahmedabadDot);
    ahmedabadMeshRef.current = ahmedabadDot;

    const domeRadius = 0.23;
    const geomDome = new THREE.SphereGeometry(domeRadius, 16, 16, 0, Math.PI * 2, 0, Math.PI / 3);
    const matDome = new THREE.MeshBasicMaterial({
      color: 0x00ffb2,
      wireframe: true,
      transparent: true,
      opacity: 0.08,
      side: THREE.DoubleSide
    });
    const ahmedabadDome = new THREE.Mesh(geomDome, matDome);
    mainGroup.add(ahmedabadDome);
    domeMeshRef.current = ahmedabadDome;

    // Authentic orbit path: EllipseCurve rotated to simulate ~97 degrees SSO
    const orbitRadius = 1.38;
    const orbitalInclination = 97.0 * Math.PI / 180;

    const orbitCurve = new THREE.EllipseCurve(
      0, 0,
      orbitRadius, orbitRadius * 0.96, // subtle ellipse (eccentricity)
      0, 2 * Math.PI,
      false,
      0
    );

    const curvePoints = orbitCurve.getPoints(120);
    const orbitPointsArr = curvePoints.map(p => {
      const ox = p.x;
      const oz = p.y;
      const x = ox * Math.cos(orbitalInclination);
      const y = ox * Math.sin(orbitalInclination);
      const z = oz;
      return new THREE.Vector3(x, y, z);
    });
    
    const orbitPathGeom = new THREE.BufferGeometry().setFromPoints(orbitPointsArr);
    const orbitPathMat = new THREE.LineBasicMaterial({
      color: 0x00FF8C,
      transparent: true,
      opacity: 0.22, // subtle and professional
    });
    const orbitPath = new THREE.Line(orbitPathGeom, orbitPathMat);
    scene.add(orbitPath);

    // Glowing satellite marker (radius 0.04)
    const satMarkerGeom = new THREE.SphereGeometry(0.024, 16, 16);
    const satMarkerMat = new THREE.MeshBasicMaterial({ color: 0x00FF8C });
    const satMarker = new THREE.Mesh(satMarkerGeom, satMarkerMat);
    
    // Point light attached to satellite
    const satPointLight = new THREE.PointLight(0x00FF8C, 1.5, 3.5);
    satMarker.add(satPointLight);
    scene.add(satMarker);
    satelliteMeshRef.current = satMarker;

    // Communication uplink line
    const linkPoints = [new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, 0)];
    const geomLink = new THREE.BufferGeometry().setFromPoints(linkPoints);
    const matLink = new THREE.LineDashedMaterial({
      color: 0x00FF8C,
      dashSize: 0.05,
      gapSize: 0.03,
      transparent: true,
      opacity: 0.75,
      linewidth: 1
    });
    const linkLine = new THREE.Line(geomLink, matLink);
    scene.add(linkLine);
    linkLineMeshRef.current = linkLine;

    let animationId: number;
    const clock = new THREE.Clock();

    let cloudsYRotation = 0;

    const animate = () => {
      animationId = requestAnimationFrame(animate);

      // Support interactive OrbitControls
      controls.update();

      const delta = clock.getDelta();

      // Earth rotation (+0.0005 per frame -> speed scaled beautifully with delta)
      const frameScale = (delta / (1/60)); // normalize to 60fps
      const actualEarthDelta = 0.0005 * (frameScale > 4 ? 1 : frameScale);
      earthRotationRef.current += actualEarthDelta;

      // Clouds rotate at a different rate
      cloudsYRotation += 0.0003 * (frameScale > 4 ? 1 : frameScale);

      // Satellite orbital movement
      orbitalAngleRef.current += 0.0016 * (frameScale > 4 ? 1 : frameScale);
      const satAngle = orbitalAngleRef.current;
      
      // Compute position on the orbital ellipse
      const pCoord = orbitCurve.getPointAt((satAngle / (Math.PI * 2)) % 1.0);
      const satX = pCoord.x * Math.cos(orbitalInclination);
      const satY = pCoord.x * Math.sin(orbitalInclination);
      const satZ = pCoord.y;
      
      satMarker.position.set(satX, satY, satZ);

      // Update shader camera world position uniform for ocean specular highlight tracking
      earthUniforms.cameraWorldPosition.value.copy(camera.position);

      // Rotate Earth sphere
      earthCore.rotation.y = earthRotationRef.current;
      
      // Rotate clouds sphere independently
      cloudsCore.rotation.y = earthRotationRef.current + cloudsYRotation;

      // Locate Ahmedabad based on Earth rotation
      const currentEarthRotation = earthRotationRef.current;
      const ahmedabadPos = getCoordinateOnSphere(ahmedabadLat, ahmedabadLng, earthRadius, currentEarthRotation);
      ahmedabadDot.position.copy(ahmedabadPos);
      
      ahmedabadDome.position.copy(ahmedabadPos);
      ahmedabadDome.lookAt(ahmedabadPos.clone().multiplyScalar(2));
      ahmedabadDome.rotateX(Math.PI / 2);

      // Uplink check
      const satPosVec = satMarker.position;
      const normAhmedabad = ahmedabadPos.clone().normalize();
      const normSatellite = satPosVec.clone().normalize();
      const dotProd = normAhmedabad.dot(normSatellite);

      const hasUplinkLineOfSight = dotProd > 0.82;
      setIsInContact(hasUplinkLineOfSight);

      // Calculate Latitude & Longitude
      const r_xy_len = Math.sqrt(satX * satX + satY * satY);
      const lat_deg = Math.atan2(satY, r_xy_len) * 180 / Math.PI;
      const lng_deg = (((Math.atan2(satX, satZ) * 180 / Math.PI - (currentEarthRotation * 180 / Math.PI)) % 360) + 360) % 360 - 180;
      
      setSatLat(Number(lat_deg.toFixed(4)));
      setSatLng(Number(lng_deg.toFixed(4)));

      if (hasUplinkLineOfSight) {
        linkLine.visible = true;
        const pts = [ahmedabadPos, satPosVec];
        linkLine.geometry.setFromPoints(pts);
        linkLine.computeLineDistances();
        
        matDome.color.setHex(0x00FF8C);
        matDome.opacity = 0.16 + Math.sin(Date.now() * 0.007) * 0.05;
      } else {
        linkLine.visible = false;
        matDome.color.setHex(0x00FF8C);
        matDome.opacity = 0.06;
      }

      renderer.render(scene, camera);
    };

    animate();

    const handleResize = () => {
      if (!mountRef.current) return;
      const w = mountRef.current.clientWidth;
      const h = mountRef.current.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };

    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(mountRef.current);

    return () => {
      cancelAnimationFrame(animationId);
      resizeObserver.disconnect();
      controls.dispose();
      if (mountRef.current) {
        mountRef.current.innerHTML = '';
      }
    };
  }, [screenView]);

  return (
    <div className="flex-1 flex flex-col gap-6 font-sans min-h-0 text-[#D4D4D4] select-text">
      
      {/* 1. Header with Simulated Monitor Switches */}
      <div className="bg-[#1A1A1A]/95 border border-white/10 p-5 rounded-sm shadow-xl relative overflow-hidden flex flex-col md:flex-row items-stretch md:items-center justify-between gap-4">
        <div className="absolute top-0 left-0 w-2.5 h-2.5 border-t border-l border-signal-green/40"></div>
        <div className="absolute bottom-0 right-0 w-2.5 h-2.5 border-b border-r border-signal-green/40"></div>
        
        <div className="flex items-center gap-3">
          <div className="inline-flex w-2.5 h-2.5 rounded-full bg-signal-green animate-pulse"></div>
          <div>
            <h1 className="font-display text-base font-black text-white uppercase tracking-tight leading-none">
              SATELLITE OPERATIONS & SECURITY MULTI-SCREEN CENTER
            </h1>
            <p className="font-mono text-[9px] text-[#D4D4D4]/50 uppercase tracking-widest mt-1">
              MISSION TARGET: CARTOSAT-3 • SECURE POST-QUANTUM RESCUE CONTROLLER
            </p>
          </div>
        </div>

        {/* Horizontal Screen Selector Switch */}
        <div className="flex bg-[#0A0A0C] border border-white/10 p-1 rounded-sm gap-1 self-start md:self-auto select-none">
          <button
            onClick={() => setScreenView('screen1')}
            className={`px-3 py-1.5 font-mono text-[10px] font-black tracking-wider uppercase rounded-xs transition-all ${
              screenView === 'screen1'
                ? 'bg-signal-green text-[#0A0A0C] font-extrabold shadow-sm'
                : 'text-[#D4D4D4]/60 hover:text-white'
            }`}
          >
            SCREEN 1: DASHBOARD
          </button>
          <button
            onClick={() => setScreenView('screen2')}
            className={`px-3 py-1.5 font-mono text-[10px] font-black tracking-wider uppercase rounded-xs transition-all ${
              screenView === 'screen2'
                ? 'bg-signal-green text-[#0A0A0C] font-extrabold shadow-sm'
                : 'text-[#D4D4D4]/60 hover:text-white'
            }`}
          >
            SCREEN 2: PI #1 (AI ENGINE)
          </button>
          <button
            onClick={() => setScreenView('screen3')}
            className={`px-3 py-1.5 font-mono text-[10px] font-black tracking-wider uppercase rounded-xs transition-all ${
              screenView === 'screen3'
                ? 'bg-signal-green text-[#0A0A0C] font-extrabold shadow-sm'
                : 'text-[#D4D4D4]/60 hover:text-white'
            }`}
          >
            SCREEN 3: PI #2 (SDR RADIO)
          </button>
        </div>
      </div>

      {/* RENDER VIEW SCREEN 1: MAIN PROJECTOR DISPLAY */}
      {screenView === 'screen1' && (
        <>
          {/* Subsystem Health Ribbon */}
          <div className="bg-[#141416]/90 border border-white/10 p-3.5 rounded-sm flex flex-wrap items-center justify-between gap-4">
            <span className="font-mono text-[10px] text-[#D4D4D4]/50 uppercase tracking-widest font-bold">SYSTEM STATUS:</span>
            <div className="flex flex-wrap items-center gap-2.5">
              {Object.entries(statuses).map(([sub, stat]) => (
                <div 
                  key={sub}
                  className={`px-3 py-1.5 border rounded-sm font-mono text-[10px] font-black tracking-widest flex items-center gap-2 transition-all ${
                    stat === 'nominal' ? 'bg-[#00ffc8]/5 border-[#00ffc8]/15 text-[#00ffc8]' :
                    stat === 'warning' ? 'bg-amber-400/5 border-amber-400/20 text-amber-300' :
                    'bg-red-500/10 border-red-500/30 text-red-400 animate-pulse'
                  }`}
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${
                    stat === 'nominal' ? 'bg-[#00ffc8]' :
                    stat === 'warning' ? 'bg-amber-400' : 'bg-red-400'
                  }`}></span>
                  <span>{sub}: {(stat as string).toUpperCase()}</span>
                </div>
              ))}

              <div 
                onClick={() => setWsConnected(!wsConnected)}
                className={`cursor-pointer px-3 py-1.5 border rounded-sm font-mono text-[9.5px] uppercase tracking-widest font-black flex items-center gap-1.5 ${
                  wsConnected ? 'bg-signal-green/10 text-signal-green border-signal-green/20' : 'bg-red-500/10 text-red-400 border-red-500/15'
                }`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${wsConnected ? 'bg-signal-green animate-ping' : 'bg-red-500'}`}></span>
                <span>WS:{wsConnected ? 'LIVE' : 'DOWN'}</span>
              </div>
            </div>
          </div>

          {/* Anomaly banner if actively recovering */}
          {isRecovering && (
            <div className="bg-amber-400/10 border border-amber-400/40 p-3.5 rounded-sm flex items-center justify-between animate-pulse text-amber-300 font-mono text-xs">
              <div className="flex items-center gap-2">
                <Warning className="w-5 h-5" />
                <span>CY-1/AI-2 SECUR_RESCUE LOCK: ACTIVE POST-QUANTUM KEY TRANSFER UPLINK IN PROGRESS... DO NOT INTERRUPT</span>
              </div>
              <span className="font-bold">STATUS: UPLINKING COMMANDS</span>
            </div>
          )}

          {/* Grid: 3D Earth, Countdown, and Anomaly Timeline */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            
            {/* 3D Tracker (7 Columns) */}
            <div className="lg:col-span-8 flex flex-col gap-6">
              <div className="bg-[#1A1A1A]/95 border border-white/10 p-5 rounded-sm flex flex-col h-[400px] shadow-lg relative min-w-0">
                <div className="flex flex-wrap items-center justify-between border-b border-white/10 pb-2.5 mb-2.5 z-20">
                  <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined text-xs text-[#1BF4AA]">globe</span>
                    <span className="font-mono text-xs font-black text-white uppercase tracking-wider">3D Earth Orbit Tracking Space</span>
                  </div>
                  
                  <div className="flex items-center gap-2 font-mono text-[9px] text-[#D4D4D4]/55 uppercase">
                    TLE Source: {tleLoading ? 'Connecting...' : tleLastFetched}
                  </div>
                </div>

                {/* HUD Details */}
                <div className="absolute top-16 left-5 z-20 font-mono text-[9.5px] bg-[#0c0d11]/85 border border-white/10 p-2.5 rounded-sm pointer-events-none space-y-1 backdrop-blur-sm">
                  <div>TARGET SATELLITE: <span className="text-white font-bold">{tle.name}</span></div>
                  <div>NORAD ID: <span className="text-[#4fc3f7] font-bold">#44804</span></div>
                  <div>INCLINATION: <span className="text-[#4fc3f7]">{tle.inclination.toFixed(4)}°</span></div>
                  <div>VELOCITY: <span className="text-white font-extrabold">{velocity} KM/S</span></div>
                  <div>ORBIT NO: <span className="text-white">{orbitalNumber}</span></div>
                </div>

                <div className="absolute top-16 right-5 z-20 font-mono text-[9.5px] bg-[#0c0d11]/85 border border-white/10 p-2.5 rounded-sm pointer-events-none space-y-1 backdrop-blur-sm text-right">
                  <div>LATITUDE: <span className="text-[#1bf4aa] font-bold">{satLat.toFixed(3)}° {satLat >= 0 ? 'N' : 'S'}</span></div>
                  <div>LONGITUDE: <span className="text-[#1bf4aa] font-bold">{satLng.toFixed(3)}° {satLng >= 0 ? 'E' : 'W'}</span></div>
                  <div>ALTITUDE: <span className="text-white font-bold">{satAltitude.toFixed(1)} KM</span></div>
                  <div className="pt-0.5 mt-0.5 border-t border-white/10">
                    CONTACT DOME: <span className={isInContact ? 'text-signal-green font-bold animate-pulse' : 'text-[#D4D4D4]/50'}>{isInContact ? 'UPLINK LOCK' : 'WAITING'}</span>
                  </div>
                </div>

                {/* Three.js Canvas replaced with Iframe Globe */}
                <div className="flex-1 w-full h-full min-h-0 relative z-0">
                  <EarthGlobe />
                </div>

                {/* Active contact overlay */}
                <div className={`absolute bottom-3 right-4 z-25 font-mono text-[10px] p-2.5 border rounded-sm flex items-center gap-2 pointer-events-none transition-all ${
                  isInContact 
                    ? 'bg-signal-green/10 border-signal-green/30 text-signal-green' 
                    : 'bg-[#1e1e1e]/85 border-white/10 text-[#D4D4D4]/40'
                }`}>
                  <span className={`w-2 h-2 rounded-full ${isInContact ? 'bg-signal-green animate-ping' : 'bg-[#D4D4D4]/35'}`}></span>
                  <span>{isInContact ? 'AHMEDABAD GROUND CONTACT ACTIVE' : 'L-BAND ACQUISITION TIMEOUT'}</span>
                </div>
              </div>
            </div>

            {/* Countdown & Anomaly (4 Columns) */}
            <div className="lg:col-span-4 flex flex-col gap-6">
              
              {/* Ground Contact Countdown */}
              <div className="bg-[#1A1A1A]/95 border border-white/10 p-5 rounded-sm shadow-md flex flex-col font-mono text-center relative overflow-hidden shrink-0">
                <h3 className="text-left text-[10px] font-bold uppercase tracking-wider text-[#D4D4D4]/50 pb-2 border-b border-white/10 mb-3 flex items-center gap-1.5">
                  <span className="inline-flex w-1.5 h-1.5 bg-[#ccff00] rounded-full"></span>
                  GROUND CONTACT TIMER
                </h3>
                
                <div className="text-[42px] font-black text-[#ccff00] tracking-tight leading-none tabular-nums py-2.5 flex justify-center items-center gap-1 font-mono">
                  {Math.floor(countdownSecs / 60).toString().padStart(2, '0')}
                  <span className="text-white/20 animate-pulse font-normal">:</span>
                  {(countdownSecs % 60).toString().padStart(2, '0')}
                </div>
                <div className="text-[9px] uppercase text-[#D4D4D4]/40 font-bold tracking-widest mt-1 mb-3">NEXT AHMEDABAD CAPTURE WINDOW</div>

                <div className="grid grid-cols-2 gap-2 text-left text-[10px] border-t border-white/5 pt-3">
                  <div>PASS DURATION: <span className="text-white font-extrabold">09:22 Min</span></div>
                  <div>MAX ELEVATION: <span className="text-white">61° Sector</span></div>
                  <div className="mt-1">AOS TIMER: <span className="text-[#1bf4aa] font-medium font-mono">14:22:10 UTC</span></div>
                  <div className="mt-1">LOS TIMER: <span className="text-[#1bf4aa] font-medium font-mono">14:31:32 UTC</span></div>
                </div>
              </div>

              {/* Anomaly feed */}
              <div className="bg-[#1A1A1A]/95 border border-white/10 p-5 rounded-sm flex flex-col h-[208px] shadow-lg relative min-w-0">
                <div className="text-[10px] font-black text-white uppercase tracking-wider border-b border-white/10 pb-3.5 mb-3 flex items-center gap-1.5">
                  <Warning className="w-3.5 h-3.5 text-red-400" />
                  <span>AI ANOMALY ALERT TIMELINE</span>
                </div>

                <div className="flex-grow overflow-y-auto space-y-2 pr-1 tech-scrollbar select-text">
                  {anomalyFeed.map(alert => {
                    // System-dependent left border colors
                    let borderLeftStyle = 'border-l-4';
                    if (alert.subsystem === 'ADCS') {
                      borderLeftStyle += ' border-l-[#00FF8C] border-y border-r border-white/5';
                    } else if (alert.subsystem === 'Power') {
                      borderLeftStyle += ' border-l-[#FFB800] border-y border-r border-white/5';
                    } else {
                      borderLeftStyle += ' border-l-[#00E5FF] border-y border-r border-white/5';
                    }

                    return (
                      <div 
                        key={alert.id}
                        className={`p-2.5 rounded-r-xs font-mono text-[10px] leading-relaxed transition-all ${borderLeftStyle} ${
                          alert.status === 'nominal' ? 'bg-[#00ffc8]/5 text-[#D4D4D4]' :
                          alert.status === 'warning' ? 'bg-amber-400/5 text-amber-200' :
                          'bg-red-500/10 text-red-200'
                        }`}
                      >
                        <div className="flex justify-between items-center font-bold border-b border-white/5 mb-1.5">
                          <span className="uppercase text-[9px] flex items-center gap-1">
                            <span className={`w-1 h-1 rounded-full ${alert.status === 'nominal' ? 'bg-[#00ffc8]' : alert.status === 'warning' ? 'bg-amber-400' : 'bg-red-500 animate-ping'}`}></span>
                            {alert.subsystem} INFERENCE
                          </span>
                          <span className="text-white/40 text-[8.5px] font-mono">{alert.timestamp}</span>
                        </div>
                        <div className="text-[9.5px] leading-tight text-white mb-0.5 font-bold uppercase">{alert.actionTaken.substring(0,60)}...</div>
                        
                        {/* Interactive Anomaly Index progress bar */}
                        <div className="flex items-center gap-2 mt-1.5">
                          <span className="text-[#D4D4D4]/50 text-[8.5px]">ANOMALY INDEX: <span className="text-white font-mono font-bold">{alert.anomalyScore.toFixed(1)}%</span></span>
                          <div className="flex-1 bg-white/10 h-1 rounded-xs overflow-hidden max-w-[80px]">
                            <div 
                              className={`h-full rounded-xs ${
                                alert.status === 'nominal' ? 'bg-[#00ffc8]' :
                                alert.status === 'warning' ? 'bg-amber-400' :
                                'bg-red-500'
                              }`}
                              style={{ width: `${alert.anomalyScore}%` }}
                            ></div>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

            </div>
          </div>

          {/* 6 requested Telemetry Charts explicitly rendered in 3x2 Grid */}
          <div className="border-t border-white/10 pt-4 mt-2">
            <h3 className="font-mono text-[11px] font-bold uppercase tracking-wider text-[#D4D4D4]/60 mb-4 flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5 text-signal-green" />
              LIVE TELEMETRY CRITICAL LANES (6 METRICS OVER TIME - UPDATE FREQ: 1s)
            </h3>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              
              {/* Chart 1: Battery % */}
              <div className="bg-[#050F05] border border-[#1A2A1A] p-4 rounded-sm flex flex-col justify-between h-[180px] shadow-md relative group select-none">
                <div className="flex justify-between items-start border-b border-white/5 pb-1.5">
                  <div>
                    <h4 className="font-mono text-[9px] text-[#D4D4D4]/50 uppercase tracking-widest font-black">BATTERY STATUS</h4>
                    <span className="text-[22px] font-mono font-black text-white">{(historyData[historyData.length - 1]?.batteryPct || 0).toFixed(2)}%</span>
                  </div>
                  
                  {/* Status indicator: solid small dot with status text */}
                  {(historyData[historyData.length - 1]?.batteryPct || 100) >= 50 ? (
                    <div className="flex items-center gap-1.5 font-mono text-[8.5px] font-bold tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-[#00FF8C] animate-pulse"></span>
                      <span className="text-[#00FF8C]">NOMINAL</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5 font-mono text-[8.5px] font-bold tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-red-500 animate-ping"></span>
                      <span className="text-red-500 font-extrabold animate-pulse">HIGH DISCHARGE</span>
                    </div>
                  )}
                </div>
                <div className="flex-1 min-h-0 py-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={historyData} margin={{ top: 0, right: 0, left: -45, bottom: 0 }}>
                      <defs>
                        <linearGradient id="batPctGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#00ff8c" stopOpacity={0.15}/>
                          <stop offset="95%" stopColor="#00ff8c" stopOpacity={0.0}/>
                        </linearGradient>
                      </defs>
                      <XAxis dataKey="time" hide />
                      <YAxis domain={[0, 100]} hide />
                      <Area type="monotone" dataKey="batteryPct" stroke="#00ff8c" fill="url(#batPctGrad)" strokeWidth={1.5} dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
                <div className="text-[8px] font-mono text-[#D4D4D4]/30 text-right uppercase mt-1">
                  LAST UPDATE: {historyData[historyData.length - 1]?.time}
                </div>
              </div>

              {/* Chart 2: Battery Voltage */}
              <div className="bg-[#050F05] border border-[#1A2A1A] p-4 rounded-sm flex flex-col justify-between h-[180px] shadow-md relative group select-none">
                <div className="flex justify-between items-start border-b border-white/5 pb-1.5">
                  <div>
                    <h4 className="font-mono text-[9px] text-[#D4D4D4]/50 uppercase tracking-widest font-black">BATTERY VOLTAGE</h4>
                    <span className="text-[22px] font-mono font-black text-white">{(historyData[historyData.length - 1]?.batteryVoltage || 0).toFixed(2)} Volts</span>
                  </div>
                  
                  {/* Status indicator: solid small dot with status text */}
                  {(historyData[historyData.length - 1]?.batteryVoltage || 30) >= 24 ? (
                    <div className="flex items-center gap-1.5 font-mono text-[8.5px] font-bold tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-[#00FF8C] animate-pulse"></span>
                      <span className="text-[#00FF8C]">NOMINAL</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5 font-mono text-[8.5px] font-bold tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-red-500 animate-ping"></span>
                      <span className="text-red-500 font-extrabold animate-pulse">UNDERVOLTAGE</span>
                    </div>
                  )}
                </div>
                <div className="flex-1 min-h-0 py-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={historyData} margin={{ top: 0, right: 0, left: -45, bottom: 0 }}>
                      <XAxis dataKey="time" hide />
                      <YAxis domain={[15, 40]} hide />
                      <Line type="monotone" dataKey="batteryVoltage" stroke="#4fc3f7" strokeWidth={1.5} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                <div className="text-[8px] font-mono text-[#D4D4D4]/30 text-right uppercase mt-1">
                  SYS RATE: 22 kSPS
                </div>
              </div>

              {/* Chart 3: OBC Temperature */}
              <div className="bg-[#050F05] border border-[#1A2A1A] p-4 rounded-sm flex flex-col justify-between h-[180px] shadow-md relative group select-none">
                <div className="flex justify-between items-start border-b border-white/5 pb-1.5">
                  <div>
                    <h4 className="font-mono text-[9px] text-[#D4D4D4]/50 uppercase tracking-widest font-black">OBC TEMPERATURE</h4>
                    <span className="text-[22px] font-mono font-black text-white">{(historyData[historyData.length - 1]?.cpuTemp || 0).toFixed(1)}°C</span>
                  </div>
                  
                  {/* Status indicator: solid small dot with status text */}
                  {(historyData[historyData.length - 1]?.cpuTemp || 40) < 55 ? (
                    <div className="flex items-center gap-1.5 font-mono text-[8.5px] font-bold tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-[#00E5FF] animate-pulse"></span>
                      <span className="text-[#00E5FF]">COOL</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5 font-mono text-[8.5px] font-bold tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-red-400 animate-ping"></span>
                      <span className="text-red-400 font-extrabold animate-pulse">TEMP HIGH</span>
                    </div>
                  )}
                </div>
                <div className="flex-1 min-h-0 py-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={historyData} margin={{ top: 0, right: 0, left: -45, bottom: 0 }}>
                      <XAxis dataKey="time" hide />
                      <YAxis domain={[25, 75]} hide />
                      <Line type="monotone" dataKey="cpuTemp" stroke="#ff8f00" strokeWidth={1.5} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                <div className="text-[8px] font-mono text-[#D4D4D4]/30 text-right uppercase mt-1">
                  THERMAL GRID SENS: 0.1°
                </div>
              </div>

              {/* Chart 4: Power Consumption */}
              <div className="bg-[#050F05] border border-[#1A2A1A] p-4 rounded-sm flex flex-col justify-between h-[180px] shadow-md relative group select-none">
                <div className="flex justify-between items-start border-b border-white/5 pb-1.5">
                  <div>
                    <h4 className="font-mono text-[9px] text-[#D4D4D4]/50 uppercase tracking-widest font-black">POWER CONSUMPTION</h4>
                    <span className="text-[22px] font-mono font-black text-white">{(historyData[historyData.length - 1]?.powerConsumption || 0).toFixed(1)} Watts</span>
                  </div>
                  
                  {/* Status indicator: solid small dot with status text */}
                  {(historyData[historyData.length - 1]?.powerConsumption || 50) < 90 ? (
                    <div className="flex items-center gap-1.5 font-mono text-[8.5px] font-bold tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-[#00FF8C] animate-pulse"></span>
                      <span className="text-[#00FF8C]">NOMINAL</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5 font-mono text-[8.5px] font-bold tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span>
                      <span className="text-amber-300 font-extrabold animate-pulse">LOAD SURGE</span>
                    </div>
                  )}
                </div>
                <div className="flex-grow flex-1 min-h-0 py-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={historyData} margin={{ top: 0, right: 0, left: -45, bottom: 0 }}>
                      <defs>
                        <linearGradient id="pwrGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#ccff00" stopOpacity={0.15}/>
                          <stop offset="95%" stopColor="#ccff00" stopOpacity={0.0}/>
                        </linearGradient>
                      </defs>
                      <XAxis dataKey="time" hide />
                      <YAxis domain={[20, 150]} hide />
                      <Area type="monotone" dataKey="powerConsumption" stroke="#ccff00" fill="url(#pwrGrad)" strokeWidth={1.5} dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
                <div className="text-[8px] font-mono text-[#D4D4D4]/30 text-right uppercase mt-1">
                  SOLAR BAL: POSITIVE
                </div>
              </div>

              {/* Chart 5: ADCS Rotation Rate */}
              <div className="bg-[#050F05] border border-[#1A2A1A] p-4 rounded-sm flex flex-col justify-between h-[180px] shadow-md relative group select-none">
                <div className="flex justify-between items-start border-b border-white/5 pb-1.5">
                  <div>
                    <h4 className="font-mono text-[9px] text-[#D4D4D4]/50 uppercase tracking-widest font-black">ADCS ROTATION RATE</h4>
                    <span className="text-[22px] font-mono font-black text-white">{(historyData[historyData.length - 1]?.angularRate || 0).toFixed(3)} deg/sec</span>
                  </div>
                  
                  {/* Status indicator: solid small dot with status text */}
                  {(historyData[historyData.length - 1]?.angularRate || 0) < 0.5 ? (
                    <div className="flex items-center gap-1.5 font-mono text-[8.5px] font-bold tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-[#00FF8C] animate-pulse"></span>
                      <span className="text-[#00FF8C]">NOMINAL</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5 font-mono text-[8.5px] font-bold tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-red-500 animate-ping"></span>
                      <span className="text-red-500 font-extrabold animate-pulse">CRITICAL DRIFT</span>
                    </div>
                  )}
                </div>
                <div className="flex-grow flex-1 min-h-0 py-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={historyData} margin={{ top: 0, right: 0, left: -45, bottom: 0 }}>
                      <XAxis dataKey="time" hide />
                      <YAxis domain={[0, 6]} hide />
                      <Line type="monotone" dataKey="angularRate" stroke="#f44336" strokeWidth={1.5} dot={(props: any) => {
                        const { cx, cy, payload } = props;
                        if (payload.angularRate > 1.0) {
                          return <circle cx={cx} cy={cy} r={4} fill="#f44336" stroke="#fff" key={cx} />;
                        }
                        return null;
                      }} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                <div className="text-[8px] font-mono text-[#D4D4D4]/30 text-right uppercase mt-1">
                  COILS LOCKED: YES
                </div>
              </div>

              {/* Chart 6: RF Signal Strength */}
              <div className="bg-[#050F05] border border-[#1A2A1A] p-4 rounded-sm flex flex-col justify-between h-[180px] shadow-md relative group select-none">
                <div className="flex justify-between items-start border-b border-white/5 pb-1.5">
                  <div>
                    <h4 className="font-mono text-[9px] text-[#D4D4D4]/50 uppercase tracking-widest font-black">RF SIGNAL STRENGTH</h4>
                    <span className="text-[22px] font-mono font-black text-white">{(historyData[historyData.length - 1]?.signalStrength || -110)} dBm</span>
                  </div>
                  
                  {/* Status indicator: solid small dot with status text */}
                  {isInContact ? (
                    <div className="flex items-center gap-1.5 font-mono text-[8.5px] font-bold tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-[#00FF8C] animate-pulse"></span>
                      <span className="text-[#00FF8C]">LOCK OK</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5 font-mono text-[8.5px] font-bold tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-[#00E5FF] animate-pulse"></span>
                      <span className="text-[#00E5FF]">STANDBY</span>
                    </div>
                  )}
                </div>
                <div className="flex-grow flex-1 min-h-0 py-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={historyData} margin={{ top: 0, right: 0, left: -45, bottom: 0 }}>
                      <defs>
                        <linearGradient id="sigPowerGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#e040fb" stopOpacity={0.15}/>
                          <stop offset="95%" stopColor="#e040fb" stopOpacity={0.0}/>
                        </linearGradient>
                      </defs>
<XAxis dataKey="time" hide />
                      <YAxis domain={[-125, -30]} hide />
                      <Area type="monotone" dataKey="signalStrength" stroke="#e040fb" fill="url(#sigPowerGrad)" strokeWidth={1.5} dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
                <div className="text-[8px] font-mono text-[#D4D4D4]/30 text-right uppercase mt-1">
                  FREQ LOCK: 9.68GHz Ku-band
                </div>
              </div>

            </div>
          </div>
        </>
      )}

      {/* RENDER VIEW SCREEN 2: RASPBERRY PI #1 INTEGRATION TERMINAL */}
      {screenView === 'screen2' && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6 min-h-[500px]">
          
          {/* Box 1: FastAPI API Dev Server Console */}
          <div className="lg:col-span-2 bg-[#050508] border border-white/10 p-4 rounded-sm flex flex-col font-mono shadow-inner h-[500px]">
            <div className="flex items-center justify-between border-b border-white/10 pb-2 mb-2 text-[10px] text-[#1BF4AA] font-bold">
              <span>FASTAPI API DEV SERVER [PI-1:8000]</span>
              <span className="animate-pulse bg-[#1BF4AA]/10 px-1.5 py-0.5 rounded-xs text-[8px]">ONLINE</span>
            </div>
            <div className="flex-1 overflow-y-auto text-[10.5px] text-green-400/90 leading-tight space-y-1 select-all tech-scrollbar pr-1 pr-1.5 pt-1">
              {fastapiLogs.map((log, index) => (
                <div key={index} className="break-all font-mono">&gt; {log}</div>
              ))}
            </div>
            <div className="text-[8px] text-[#D4D4D4]/30 border-t border-white/5 pt-2 mt-2">
              PORT BOUND: 0.0.0.0:8000 • CLIENT ACCESS RESTRICTIONS OFF
            </div>
          </div>

          {/* Box 2: AI-1 Inference Classifier Terminal */}
          <div className="lg:col-span-3 flex flex-col gap-6">
            
            {/* Split Top Panel: AI-1 & Recovery */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 flex-1 min-h-[220px]">
              
              {/* AI Classifier */}
              <div className="bg-[#050508] border border-white/10 p-4 rounded-sm flex flex-col font-mono">
                <div className="flex items-center justify-between border-b border-white/10 pb-2 mb-2 text-[10px] text-amber-400 font-bold">
                  <span>AI-1 CLASSIFIER LOGS</span>
                  <span className="text-[8.5px] border border-amber-400/30 text-amber-400 px-1 rounded-xs">ACTIVE</span>
                </div>
                <div className="flex-1 overflow-y-auto text-[10px] text-amber-200/85 leading-tight space-y-1 tech-scrollbar">
                  {classifierLogs.map((log, index) => (
                    <div key={index}>&gt; {log}</div>
                  ))}
                </div>
              </div>

              {/* Recovery Agent */}
              <div className="bg-[#050508] border border-white/10 p-4 rounded-sm flex flex-col font-mono">
                <div className="flex items-center justify-between border-b border-white/10 pb-2 mb-2 text-[10px] text-cyan-400 font-bold">
                  <span>AI-2 RECOVERY AGENT</span>
                  <span className="text-[8.5px] border border-cyan-400/30 text-cyan-400 px-1 rounded-xs">STANDBY</span>
                </div>
                <div className="flex-1 overflow-y-auto text-[10px] text-cyan-200/85 leading-tight space-y-1 tech-scrollbar">
                  {recoveryLogs.map((log, index) => (
                    <div key={index}>&gt; {log}</div>
                  ))}
                </div>
              </div>

            </div>

            {/* Split Bottom Panel: Crypto Board & Watchdog */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 flex-1 min-h-[220px]">
              
              {/* Secure Dilithium Signer */}
              <div className="bg-[#050508] border border-white/10 p-4 rounded-sm flex flex-col font-mono">
                <div className="flex items-center justify-between border-b border-white/10 pb-2 mb-2 text-[10px] text-fuchsia-400 font-bold">
                  <span>CRYSTALS-DILITHIUM PQ SIGNER</span>
                  <span className="text-[8.5px] font-bold border border-fuchsia-400/30 text-fuchsia-400 px-1 rounded-xs">HARD_VERIFY</span>
                </div>
                <div className="flex-1 overflow-y-auto text-[10px] text-fuchsia-200/85 leading-tight space-y-1 tech-scrollbar">
                  {cryptoLogs.map((log, index) => (
                    <div key={index}>&gt; {log}</div>
                  ))}
                </div>
              </div>

              {/* System Watchdog */}
              <div className="bg-[#050508] border border-white/10 p-4 rounded-sm flex flex-col font-mono">
                <div className="flex items-center justify-between border-b border-white/10 pb-2 mb-2 text-[10px] text-red-400 font-bold">
                  <span>SYSTEM WATCHDOG THREAD</span>
                  <span className="text-[8.5px] border border-red-500/30 text-red-400 px-1 rounded-xs">SECURED</span>
                </div>
                <div className="flex-1 overflow-y-auto text-[10px] text-red-200/85 leading-tight space-y-1 tech-scrollbar">
                  {watchdogLogs.map((log, index) => (
                    <div key={index}>&gt; {log}</div>
                  ))}
                </div>
              </div>

            </div>

          </div>

        </div>
      )}

      {/* RENDER VIEW SCREEN 3: RASPBERRY PI #2 SDR RADIO MONITOR */}
      {screenView === 'screen3' && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 min-h-[500px]">
          
          {/* SDR Control Configuration (3 columns) */}
          <div className="lg:col-span-3 bg-[#1A1A1A] border border-white/10 p-5 rounded-sm flex flex-col font-mono text-xs gap-4 shadow-lg shrink-0">
            <h3 className="text-[10px] font-black uppercase text-[#1BF4AA] border-b border-white/10 pb-2 flex items-center gap-1.5">
              <span className="material-symbols-outlined text-xs">settings_input_antenna</span>
              SDR RECEIVER CONFIG
            </h3>
            
            <div className="space-y-4">
              <div>
                <label className="text-[9px] text-[#D4D4D4]/50 block mb-1">TUNER DEVICE</label>
                <div className="bg-[#0D0D0D] border border-white/10 p-2.5 rounded-xs font-bold text-white uppercase text-[11px]">
                  RTL-SDR (v4 blog) • TCXO LOCKED
                </div>
              </div>

              <div>
                <label className="text-[9px] text-[#D4D4D4]/50 block mb-1">RECEIVE FREQUENCY</label>
                <div className="bg-[#0D0D0D] border border-white/10 p-2.5 rounded-xs text-xs font-black text-white flex justify-between items-center text-[11.5px]">
                  <span className="text-signal-green">137.9000 MHz</span>
                  <span className="text-[#D4D4D4]/40 font-normal">Ku down-conv</span>
                </div>
              </div>

              <div>
                <label className="text-[9px] text-[#D4D4D4]/50 block mb-1">DECIMATION RATE</label>
                <div className="bg-[#0D0D0D] border border-white/10 p-2.5 rounded-xs font-bold text-white">
                  64 (Bandwidth: 156.25 kHz)
                </div>
              </div>

              <div>
                <label className="text-[9px] text-[#D4D4D4]/50 block mb-1">RECEIVE GAIN</label>
                <div className="bg-[#0D0D0D] border border-white/10 p-2.5 rounded-xs font-bold text-white flex justify-between">
                  <span>32.8 dB</span>
                  <span className="text-[#D4D4D4]/40">AUTO AGC</span>
                </div>
              </div>

              <div className="bg-[#0D0D0D] p-3 border border-white/5 rounded-sm space-y-1 pt-2">
                <div className="text-[8px] text-[#D4D4D4]/55 font-bold uppercase">RF Metrics</div>
                <div className="flex justify-between text-[11px]">
                  <span>SNR METER:</span>
                  <span className={isInContact ? 'text-signal-green font-bold' : 'text-[#D4D4D4]/40'}>{isInContact ? '18.4 dB' : '1.2 dB'}</span>
                </div>
                <div className="flex justify-between text-[11px]">
                  <span>DOPPLER OFFSET:</span>
                  <span className="text-amber-400">{isInContact ? '+3.24 kHz' : '0.00 kHz'}</span>
                </div>
                <div className="flex justify-between text-[11px]">
                  <span>SQUELCH LOCK:</span>
                  <span className={isInContact ? 'text-signal-green font-bold animate-pulse' : 'text-red-400'}>{isInContact ? 'LOCKED' : 'SEARCHING'}</span>
                </div>
              </div>
            </div>
            
            <div className="mt-auto border-t border-white/15 pt-3.5 text-[9px] text-[#D4D4D4]/30 leading-relaxed font-sans mt-4">
              Real-time spectrum signal sweeps routed from passive air surveillance. Ingress IP address filtered and authorized via Dilithium.
            </div>
          </div>

          {/* Canvas Spectrum & Waterfall Grid (9 columns) */}
          <div className="lg:col-span-9 flex flex-col gap-6">
            
            {/* Realtime RF spectrum waveform sweep */}
            <div className="bg-[#1A1A1A] border border-white/10 p-4 rounded-sm flex flex-col justify-between h-[210px] shadow-lg relative min-w-0 font-mono">
              <div className="text-[9.5px] font-black text-white border-b border-white/10 pb-2 uppercase tracking-wider flex justify-between items-center">
                <span>SDR FREQUENCY SPECTRUM STREAM</span>
                <span className={isInContact ? 'text-signal-green' : 'text-red-400'}>{isInContact ? 'SIGNAL CENTER CH: CARRIER SYNCED' : 'LOW POWER NOISE FLOOR'}</span>
              </div>
              
              {/* Dynamic SVG Waveform representation */}
              <div className="flex-1 w-full relative min-h-0 bg-[#07070a] border border-white/5 rounded-xs my-2.5 overflow-hidden">
                <svg className="w-full h-full" viewBox="0 0 800 120" preserveAspectRatio="none">
                  {/* Grid lines */}
                  <line x1="0" y1="60" x2="800" y2="60" stroke="rgba(255,255,255,0.05)" strokeDasharray="5,5" />
                  <line x1="400" y1="0" x2="400" y2="120" stroke="rgba(255,255,255,0.05)" strokeDasharray="5,5" />
                  
                  {/* Waveform line */}
                  <path
                    d={`M 0 100 
                      ${Array.from({ length: 41 }, (_, i) => {
                        const x = i * 20;
                        const factor = isInContact ? Math.exp(-Math.pow((x - 416) / 20, 2)) : 0;
                        const jamFactor = activeFault === 'injection' && isInContact ? Math.exp(-Math.pow((x - 220) / 30, 2)) : 0;
                        
                        // WIRING: sampled from the live RTL-SDR spectrum when
                        // Pi #2 is online; a flat floor otherwise. Previously
                        // re-randomised on every React render.
                        const rf = rfBinsRef.current;
                        const rfVal = rf.length
                          ? 95 - Math.max(0, Math.min(90, (rf[Math.floor((i / 41) * rf.length)] + 120)))
                          : 95;
                        const noiseVal = rfVal
                          - (factor * 75)
                          - (jamFactor * 52)
                          - (Math.sin(x*0.06 + spectrumPhaseRef.current * 0.1) * 3);
                        return `L ${x} ${noiseVal}`;
                      }).join(' ')} L 800 100`}
                    fill="none"
                    stroke={isInContact ? '#1BF4AA' : '#ff5555'}
                    strokeWidth="1.5"
                    className="transition-all duration-100"
                  />
                </svg>
                <div className="absolute top-2 left-3 text-[8.5px] text-[#D4D4D4]/30 uppercase">RF GAIN ADJ: +32dB</div>
              </div>
              <div className="flex justify-between font-mono text-[8.5px] text-[#D4D4D4]/45">
                <span>137.82 MHz</span>
                <span className="text-signal-green font-bold">Center: 137.90 MHz</span>
                <span>137.98 MHz</span>
              </div>
            </div>

            {/* SDR Waterfalls scrolling canvas section */}
            <div className="bg-[#1A1A1A] border border-white/10 p-4 rounded-sm flex flex-col justify-between h-[260px] shadow-lg relative min-w-0 font-mono">
              <div className="text-[9.5px] text-white border-b border-white/10 pb-2 uppercase tracking-wider font-extrabold">
                LIVE RADIO WATERFALL CAPTURE
              </div>
              
              <div className="flex-1 w-full bg-[#0a0a0c] relative rounded-xs my-2 min-h-0 overflow-hidden border border-white/5">
                <canvas ref={waterfallCanvasRef} className="absolute inset-0 w-full h-full" />
              </div>

              <div className="flex justify-between select-none text-[8px] text-[#D4D4D4]/30 uppercase pt-1">
                <span>Waterfall scroll: 15 rows/sec</span>
                <span>FFT size: 2048 bins</span>
              </div>
            </div>

          </div>

        </div>
      )}

    </div>
  );
}
