import React, { useState, useEffect, useRef } from 'react';
import { Terminal, Shield, Cpu, Warning, Check } from './Icons';

interface CommandOutput {
  command: string;
  output: string;
  timestamp: string;
  status: 'success' | 'error' | 'info';
}

export default function OperatorPanel() {
  const [inputCommand, setInputCommand] = useState('');
  const [history, setHistory] = useState<CommandOutput[]>([
    {
      command: "SYSTEM_BOOT_LOG",
      output: "DEADSAT-RESURRECTION INTEGRITY DIAGNOSTIC v2.4_SECURE.\nUPLINK CHANNELS: CONNECTED.\nPQC AUTHENTICATION RINGS: ACTIVE.\nType HELP for full commands catalog.",
      timestamp: new Date().toLocaleTimeString(),
      status: 'info'
    }
  ]);
  const consoleBottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (consoleBottomRef.current) {
      consoleBottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [history]);

  const handleCommandSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const cmd = inputCommand.trim();
    if (!cmd) return;

    let reply = '';
    let stat: 'success' | 'error' | 'info' = 'success';
    const lowerCmd = cmd.toLowerCase();

    if (lowerCmd === 'help') {
      reply = "AVAILABLE ORBITAL COMMANDS DIRECTORY:\n" +
              "-----------------------------------------\n" +
              "  HELP            - Displays this commands guide.\n" +
              "  STABILIZE       - Sends attitude stabilization correction signals to ADCS coils.\n" +
              "  PING            - Executes high-frequency Ku-band range ping test.\n" +
              "  SECURITY_AUDIT  - Conducts full cryptographic integrity scan across active boot sectors.\n" +
              "  INJECT_KEY_A3   - Injects fresh high-entropy lattice seeds to Dilithium buffers.\n" +
              "  SYS_STATS       - Queries physical bus voltage, telemetry SNR ratios, and temperature.\n" +
              "  CLEAR           - Clears terminal logs history.";
      stat = 'info';
    } else if (lowerCmd === 'stabilize') {
      reply = "TRANSMITTING ROTATIONAL STABILIZATION COEF: [pitch_delta=0.00, yaw_delta=0.00]\n" +
              "ADCS electromagnetic coils... INDUCTING...\n" +
              "Gyroscopic drag variance stabilizer... LOCKED.\n" +
              "ORBITAL STABILIZATION PROTOCOL: NOMINAL (Attitude Stabilized).";
    } else if (lowerCmd === 'ping') {
      reply = "PINGING SATELLITE (NORAD 44804) AT 9.68 GHz...\n" +
              "Transmitting payload (8 bytes: 0x9A 0x4B 0xFF 0x12 0x34 0xEE 0x00 0xFF)...\n" +
              "Telemetry return packet received in 42.18ms. SNR: 18.2 dB.\n" +
              "LINK QUALITY: EXCELLENT.";
    } else if (lowerCmd === 'security_audit') {
      reply = "EXECUTING SATELLITE FIRMWARE INTEGRITY VERIFICATION SCAN...\n" +
              "Boot loader sector signature check: CRYSTALS-Dilithium3 OK.\n" +
              "Telemetry uplink encryption index: NOMINAL.\n" +
              "No Shor threats detected on active channels. PQC security hardcode score: 100%.";
    } else if (lowerCmd === 'inject_key_a3') {
      reply = "GENERATING FRESH LATTICE ENTROPY MATRICES q10485761...\n" +
              "Injecting keyspace bits... SUCCESS.\n" +
              "Signing bootloader sector blocks...\n" +
              "Quantum key updated. Signature hash: 0x7E..2B";
    } else if (lowerCmd === 'sys_stats') {
      reply = "BUS STATE QUERY RETURNED:\n" +
              "  BUS_VOLTAGE: 118.42V (NOM).\n" +
              "  PROPELLANT_RESERVE: 94.18% (STABLE).\n" +
              "  SOLAR_ORIENTATION: NOMINAL CELL CHARGE.\n" +
              "  THERMAL_METER: 291.15 K.";
    } else if (lowerCmd === 'clear') {
      setHistory([]);
      setInputCommand('');
      return;
    } else {
      reply = `COMMAND REJECTED: '${cmd}' is unknown.\nType HELP for terminal operations directory.`;
      stat = 'error';
    }

    const newHistory: CommandOutput = {
      command: cmd,
      output: reply,
      timestamp: new Date().toLocaleTimeString(),
      status: stat
    };

    setHistory(prev => [...prev, newHistory]);
    setInputCommand('');
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
