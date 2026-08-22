import React, { useState, useEffect, useRef } from 'react';
import { Terminal, Shield, Cpu, Warning, Check } from './Icons';
// WIRING: every command below now queries the real backend.
import { API_BASE, api } from '../api';

interface CommandOutput {
  command: string;
  output: string;
  timestamp: string;
  status: 'success' | 'error' | 'info';
}

export default function OperatorPanel() {
  const [inputCommand, setInputCommand] = useState('');
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<CommandOutput[]>([]);
  const consoleBottomRef = useRef<HTMLDivElement>(null);

  // WIRING: the boot banner reports the real connection state instead of
  // asserting "UPLINK CHANNELS: CONNECTED / PQC RINGS: ACTIVE" unconditionally.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const l: any = await api.links();
        if (!alive) return;
        const lines = Object.entries(l.links)
          .map(([k, v]: [string, any]) => `  ${k.padEnd(18)} ${v.connected ? 'OK  ' : 'DOWN'}  ${v.detail}`)
          .join('\n');
        setHistory([{
          command: 'SYSTEM_BOOT_LOG',
          output: `DEADSAT-RESURRECTION — ground segment link check\nEndpoint: ${API_BASE}\n${lines}\n\nType HELP for the command directory.`,
          timestamp: new Date().toLocaleTimeString(),
          status: l.all_connected ? 'info' : 'error',
        }]);
      } catch (e: any) {
        if (!alive) return;
        setHistory([{
          command: 'SYSTEM_BOOT_LOG',
          output: `BACKEND UNREACHABLE at ${API_BASE}\n${e.message}\n\nCheck VITE_API_BASE and that the API is running.`,
          timestamp: new Date().toLocaleTimeString(),
          status: 'error',
        }]);
      }
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (consoleBottomRef.current) {
      consoleBottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [history]);

  const push = (command: string, output: string, status: CommandOutput['status']) =>
    setHistory(prev => [...prev, { command, output, timestamp: new Date().toLocaleTimeString(), status }]);

  const handleCommandSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cmd = inputCommand.trim();
    if (!cmd || busy) return;
    const lowerCmd = cmd.toLowerCase();
    setInputCommand('');

    if (lowerCmd === 'clear') { setHistory([]); return; }

    if (lowerCmd === 'help') {
      push(cmd,
        'AVAILABLE COMMANDS (all query the live backend):\n' +
        '-----------------------------------------------\n' +
        '  HELP            - This directory.\n' +
        '  STATUS          - Satellite health from /health.\n' +
        '  SYS_STATS       - Full telemetry frame from /telemetry.\n' +
        '  PING            - Measured round-trip to the API.\n' +
        '  CONTACT         - Next ground-contact window from /contact.\n' +
        '  LINKS           - Per-component connection state.\n' +
        '  AI_STATUS       - AI-1 artifact state from /pipeline/status.\n' +
        '  CLASSIFY        - Run AI-1 over the current window.\n' +
        '  SECURITY_AUDIT  - CY-1 status, ledger depth, open alerts.\n' +
        '  LEDGER          - Recent signed-command ledger entries.\n' +
        '  RF              - RF ground station (Pi #2) status.\n' +
        '  STABILIZE       - Recover via ADCS_MEMORY_SCRUB_v2 (real uplink).\n' +
        '  RESET           - Reset the emulator to nominal.\n' +
        '  CLEAR           - Clear this terminal.', 'info');
      return;
    }

    setBusy(true);
    try {
      switch (lowerCmd) {
        case 'status': {
          const h: any = await api.health();
          push(cmd, Object.entries(h).map(([k, v]) => `  ${k.padEnd(16)} ${v}`).join('\n'), 'success');
          break;
        }
        case 'sys_stats': {
          const f: any = await api.telemetry();
          push(cmd,
            'LIVE TELEMETRY FRAME:\n' +
            `  BUS_VOLTAGE      ${f.bus_voltage_v} V\n` +
            `  BATTERY          ${f.battery_pct} %\n` +
            `  SOLAR_OUTPUT     ${f.power_w} W\n` +
            `  OBC_TEMP         ${f.obc_temp_c} °C\n` +
            `  OBC_CPU          ${f.obc_cpu_pct} %\n` +
            `  ADCS_RATE        ${f.adcs_rate_deg_s} deg/s\n` +
            `  SIGNAL           ${f.signal_strength_dbm} dBm\n` +
            `  FAULT            ${f.fault_injected ?? 'none'}\n` +
            `  FRAME_ID         ${f.frame_id}`, 'success');
          break;
        }
        case 'ping': {
          const t0 = performance.now();
          const h: any = await api.health();
          push(cmd, `PING ${API_BASE}\nReply in ${Math.round(performance.now() - t0)} ms — health: ${h.overall}`, 'success');
          break;
        }
        case 'contact': {
          const c: any = await api.contact();
          push(cmd, JSON.stringify(c, null, 2), 'success');
          break;
        }
        case 'links': {
          const l: any = await api.links();
          push(cmd, Object.entries(l.links)
            .map(([k, v]: [string, any]) => `  ${k.padEnd(18)} ${v.connected ? 'OK  ' : 'DOWN'}  ${v.detail}`)
            .join('\n'), l.all_connected ? 'success' : 'error');
          break;
        }
        case 'ai_status': {
          const p: any = await api.pipelineStatus();
          push(cmd, `  artifacts_ready  ${p.artifacts_ready}\n  missing          ${(p.missing_artifacts || []).join(', ') || 'none'}\n  seq_len          ${p.seq_len}\n  features         ${p.feature_cols?.length}\n  ${p.hint ?? ''}`,
            p.artifacts_ready ? 'success' : 'error');
          break;
        }
        case 'classify': {
          const r: any = await api.classify();
          push(cmd, `  fault_type       ${r.fault_type}\n  raw_class        ${r.raw_fault_class}\n  confidence       ${(r.confidence * 100).toFixed(2)}%\n  anomaly_flag     ${r.anomaly_flag}\n  norad_id         ${r.norad_id}`, 'success');
          break;
        }
        case 'security_audit': {
          const [s, l, a]: any[] = await Promise.all([api.cryptoStatus(), api.cryptoLedger(), api.cryptoAlerts()]);
          push(cmd,
            `  CY-1             ${s.cy1_online ? 'ONLINE' : 'OFFLINE'}\n` +
            `  mode             ${s.mode}\n` +
            `  endpoint         ${s.endpoint}\n` +
            `  ledger entries   ${(l.entries || []).length}\n` +
            `  open alerts      ${(a.alerts || []).length}`,
            s.cy1_online ? 'success' : 'error');
          break;
        }
        case 'ledger': {
          const l: any = await api.cryptoLedger();
          const rows = (l.entries || []).slice(-10);
          push(cmd, rows.length
            ? rows.map((r: any) => `  #${r.id} ${r.timestamp} ${String(r.cmd_hash).slice(0, 16)}… op=${r.operator}`).join('\n')
            : '  (ledger empty or CY-1 offline)', rows.length ? 'success' : 'error');
          break;
        }
        case 'rf': {
          const r: any = await api.rfStatus();
          push(cmd, r.online ? JSON.stringify(r.data, null, 2) : `  RF station OFFLINE\n  ${r.source}\n  ${r.error ?? ''}`, r.online ? 'success' : 'error');
          break;
        }
        case 'stabilize': {
          const r: any = await api.triggerRecovery('SEU');
          push(cmd, `RECOVERY REQUESTED — ${r.message ?? 'agent engaged'}\nWatch /ws/events for progress.`, 'success');
          break;
        }
        case 'reset': {
          await api.reset();
          push(cmd, 'Emulator reset to nominal.', 'success');
          break;
        }
        default:
          push(cmd, `COMMAND REJECTED: '${cmd}' is unknown.\nType HELP for the directory.`, 'error');
      }
    } catch (err: any) {
      push(cmd, `ERROR: ${err.message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex-1 bg-[#1A1A1A]/95 border border-signal-green/20 p-5 rounded-sm flex flex-col h-[calc(100vh-220px)] shadow-2xl relative font-mono text-sm">
      
      {/* HUD Bar */}
      <div className="flex justify-between items-center border-b border-white/10 pb-3 mb-4">
        <div className="flex items-center gap-2">
          <Terminal className="w-4 h-4 text-signal-green" />
          <span className="font-bold text-white uppercase text-xs tracking-wider">COMMAND OPERATIONS MODULE</span>
        </div>
        <span className="text-[10px] bg-signal-green/10 text-signal-green font-bold px-2 py-0.5 rounded-sm uppercase">
          SECURE_LINE: ACTV
        </span>
      </div>

      {/* Terminal View */}
      <div className="flex-1 overflow-y-auto tech-scrollbar space-y-4 pr-2 mb-4">
        {history.map((hist, idx) => (
          <div key={idx} className="space-y-1">
            {hist.command && (
              <div className="flex gap-1.5 items-center text-signal-green text-xs">
                <span className="font-bold">&gt; ORU_CON@CARTOSAT-3:</span>
                <span className="font-bold select-all">{hist.command}</span>
                <span className="text-[9px] text-[#D4D4D4]/40 ml-auto">{hist.timestamp}</span>
              </div>
            )}
            
            <div className={`p-2.5 rounded-sm border whitespace-pre-wrap text-xs leading-relaxed ${
              hist.status === 'success' ? 'bg-[#0D0D0D] border-signal-green/25 text-white' :
              hist.status === 'error' ? 'bg-threat-red/5 border-threat-red/35 text-[#FF3B30]' :
              'bg-[#0D0D0D]/90 border-white/10 text-[#D4D4D4]'
            }`}>
              {hist.output}
            </div>
          </div>
        ))}
        <div ref={consoleBottomRef}></div>
      </div>

      {/* Input row */}
      <form onSubmit={handleCommandSubmit} className="relative mt-auto">
        <div className="relative flex items-center">
          <span className="text-signal-green absolute left-3 font-bold select-none">&gt;</span>
          <input 
            type="text" 
            value={inputCommand}
            onChange={e => setInputCommand(e.target.value)}
            placeholder="Type command here (e.g., STABILIZE) and press ENTER..."
            className="w-full bg-[#0D0D0D] border border-signal-green/30 text-white font-semibold p-3.5 pl-7 rounded-sm focus:outline-none focus:border-signal-green focus:ring-1 focus:ring-signal-green text-xs"
            autoFocus
          />
          <button 
            type="submit"
            className="bg-signal-green text-black font-bold text-[11px] uppercase tracking-widest px-5 py-3.5 absolute right-0 top-0 bottom-0 hover:bg-[#D4FF00] cursor-pointer"
          >
            EXECUTE
          </button>
        </div>
      </form>
    </div>
  );
}
