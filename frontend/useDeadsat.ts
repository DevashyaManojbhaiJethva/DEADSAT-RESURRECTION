/**
 * useDeadsat.ts — live backend state for the DeadSat dashboard
 * ==============================================================
 * One hook that owns the connection to Pi #1 and exposes:
 *
 *   telemetry   — mapped into the UI's existing TelemetryState shape
 *   frame       — the raw backend telemetry frame
 *   logs        — system log entries derived from real agent events
 *   events      — raw agent/pipeline events off /ws/events
 *   links       — connection health for every component (incl. Pi #2 RF)
 *   connected   — is the telemetry WebSocket up?
 *
 * If the backend is unreachable, the hook retains the last server-provided
 * state (or explicit zero/offline placeholders before the first frame). It
 * never manufactures a telemetry stream in the browser.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AgentEvent,
  LinkStatus,
  TelemetryFrame,
  api,
  frameToTelemetryState,
  subscribeEvents,
  subscribeTelemetry,
} from './api';
import { SystemLog, TelemetryState } from './types';

const INITIAL_TELEMETRY: TelemetryState = {
  powerArray: 0,
  adcsPitch: 0,
  adcsYaw: 0,
  adcsStability: 'CRITICAL',
  commsBandwidth: 0,
  obcCpu: 0,
  obcMem: 0,
  altitude: 0,
  velocity: 0,
  lat: 0,
  lng: 0,
  temperature: 0,
};

let logSeq = 0;
function mkLog(
  message: string,
  type: SystemLog['type'],
  category: string,
): SystemLog {
  logSeq += 1;
  return {
    id: `${Date.now()}-${logSeq}`,
    timestamp: new Date().toLocaleTimeString('en-GB', { hour12: false }),
    message,
    type,
    category,
  };
}

/** Turn an agent/pipeline event into a human-readable console line. */
function eventToLog(e: AgentEvent): SystemLog | null {
  const p = e.payload ?? {};
  switch (e.event) {
    case 'fault_injected':
      return mkLog(
        `FAULT INJECTED: ${p.fault_type ?? 'unknown'} — emulator state degraded`,
        'critical',
        'fault',
      );
    case 'recovery_started':
      return mkLog(
        `AI-2 recovery agent engaged for ${p.fault_type ?? 'fault'}`,
        'warning',
        'recovery',
      );
    case 'recovery_complete':
      return mkLog(
        p.success
          ? `RECOVERY SUCCESS via ${p.procedure_used} (${p.attempts} attempt(s), ${p.elapsed_s}s)`
          : `RECOVERY FAILED: ${p.error ?? 'all procedures exhausted'}`,
        p.success ? 'nominal' : 'critical',
        'recovery',
      );
    case 'pipeline_started':
      return mkLog(
        `Pipeline started — ${p.fault_type}${p.skip_classifier ? ' (AI-1 bypassed)' : ' (AI-1 -> AI-2)'}`,
        'info',
        'pipeline',
      );
    case 'pipeline_complete':
      return mkLog(
        p.success
          ? `Pipeline complete — classified ${p.classified_fault} @ ${
              typeof p.classifier_confidence === 'number'
                ? (p.classifier_confidence * 100).toFixed(1) + '%'
                : 'n/a'
            }, recovered via ${p.procedure_used}`
          : `Pipeline finished without recovery: ${p.error ?? 'unknown'}`,
        p.success ? 'nominal' : 'critical',
        'pipeline',
      );
    case 'pipeline_failed':
      return mkLog(`Pipeline error: ${p.error}`, 'critical', 'pipeline');
    case 'uplink_sent':
      return mkLog(
        `Signed uplink transmitted: ${p.procedure_name} (${p.commands_count} commands)`,
        'info',
        'security',
      );
    default:
      return mkLog(`${e.event}: ${JSON.stringify(p).slice(0, 120)}`, 'info', 'system');
  }
}

export function useDeadsat() {
  const [telemetry, setTelemetry] = useState<TelemetryState>(INITIAL_TELEMETRY);
  const [frame, setFrame] = useState<TelemetryFrame | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [logs, setLogs] = useState<SystemLog[]>([
    mkLog('Console initialised — connecting to ground segment...', 'info', 'system'),
  ]);
  const [links, setLinks] = useState<LinkStatus | null>(null);
  const [connected, setConnected] = useState(false);

  // Keeps the ground-track/altitude continuous across frames.
  const prevRef = useRef({ lat: 32.51, lng: 122.36, altitude: 402.18, velocity: 7.672 });
  const wasConnected = useRef(false);

  const pushLog = useCallback((log: SystemLog) => {
    setLogs((prev) => [...prev.slice(-199), log]);
  }, []);

  // ── Telemetry stream ────────────────────────────────────────────────
  useEffect(() => {
    const sock = subscribeTelemetry(
      (f) => {
        setFrame(f);
        setTelemetry(() => {
          const mapped = frameToTelemetryState(f, prevRef.current);
          prevRef.current = {
            lat: mapped.lat,
            lng: mapped.lng,
            altitude: mapped.altitude,
            velocity: mapped.velocity,
          };
          return mapped as TelemetryState;
        });
      },
      (isUp) => {
        setConnected(isUp);
        if (isUp && !wasConnected.current) {
          pushLog(mkLog('Telemetry downlink ACQUIRED — live frames streaming', 'nominal', 'network'));
        } else if (!isUp && wasConnected.current) {
          pushLog(mkLog('Telemetry downlink LOST — retrying...', 'warning', 'network'));
        }
        wasConnected.current = isUp;
      },
    );
    return () => sock.close();
  }, [pushLog]);

  // ── Agent / pipeline event stream ───────────────────────────────────
  useEffect(() => {
    const sock = subscribeEvents((e) => {
      setEvents((prev) => [...prev.slice(-99), e]);
      const log = eventToLog(e);
      if (log) pushLog(log);
    });
    return () => sock.close();
  }, [pushLog]);

  // ── Link health poll ────────────────────────────────────────────────
  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const l = await api.links();
        if (alive) setLinks(l);
      } catch {
        if (alive) setLinks(null);
      }
    };
    check();
    const timer = setInterval(check, 15000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Commands ────────────────────────────────────────────────────────

  const injectFault = useCallback(
    async (backendFault: Parameters<typeof api.injectFault>[0]) => {
      try {
        await api.injectFault(backendFault);
        return true;
      } catch (e: any) {
        pushLog(mkLog(`Fault injection failed: ${e.message}`, 'critical', 'fault'));
        return false;
      }
    },
    [pushLog],
  );

  const runPipeline = useCallback(
    async (
      backendFault: Parameters<typeof api.runPipeline>[0],
      opts?: { skipClassifier?: boolean },
    ) => {
      try {
        await api.runPipeline(backendFault, opts);
        return true;
      } catch (e: any) {
        pushLog(mkLog(`Pipeline start failed: ${e.message}`, 'critical', 'pipeline'));
        return false;
      }
    },
    [pushLog],
  );

  const triggerRecovery = useCallback(
    async (backendFault: Parameters<typeof api.triggerRecovery>[0]) => {
      try {
        await api.triggerRecovery(backendFault);
        return true;
      } catch (e: any) {
        pushLog(mkLog(`Recovery trigger failed: ${e.message}`, 'critical', 'recovery'));
        return false;
      }
    },
    [pushLog],
  );

  const ping = useCallback(async () => {
    const t0 = performance.now();
    try {
      await api.health();
      const ms = Math.round(performance.now() - t0);
      pushLog(mkLog(`PING ground segment — TRANS_OK in ${ms}ms`, 'nominal', 'network'));
    } catch (e: any) {
      pushLog(mkLog(`PING failed — ${e.message}`, 'critical', 'network'));
    }
  }, [pushLog]);

  return {
    telemetry,
    frame,
    logs,
    events,
    links,
    connected,
    simulated: !connected,
    injectFault,
    runPipeline,
    triggerRecovery,
    ping,
    pushLog,
  };
}
