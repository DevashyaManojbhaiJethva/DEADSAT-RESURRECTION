import { useState, useEffect, useRef } from 'react';
import { Shield, Radio, Activity, Cpu, Warning, Check } from './Icons';

// TLE and other structures matching our design style
interface FaultOption {
  value: 'seu' | 'leak' | 'injection' | 'battery_fail' | 'adcs_fail';
  label: string;
  subsystem: 'ADCS' | 'Power' | 'OBC' | 'Comms';
  faultType: string;
  register: string;
  score: number;
  indicator: 'NATURAL' | 'ATTACK';
  indicatorText: string;
  badgeColor: string;
}

const FAULT_DATABASE: Record<'seu' | 'leak' | 'injection' | 'battery_fail' | 'adcs_fail', FaultOption> = {
  seu: {
    value: 'seu',
    label: 'Single Event Upset (SEU) - Gyro Torque Ion Drift',
    subsystem: 'ADCS',
    faultType: 'Gyroscopic Sensor Register Corruption',
    register: '0xADCS_GYRO_Y_FLT (Bit 14 flipped by cosmic ray)',
    score: 96.4,
    indicator: 'NATURAL',
    indicatorText: 'NATURAL COSMIC DRIFT DETECTED',
    badgeColor: 'border-[#1BF4AA] text-[#1BF4AA] bg-[#1BF4AA]/10',
  },
  leak: {
    value: 'leak',
    label: 'Software Memory Overrun - Task Buffer Leak',
    subsystem: 'OBC',
    faultType: 'Dynamic Stack Pointer Heap Overflow',
    register: '0xSP_OBC_LEAK (Thread stack corrupted)',
    score: 89.2,
    indicator: 'NATURAL',
    indicatorText: 'SOFTWARE CORRUPTION - IDLE FAILURE',
    badgeColor: 'border-[#1BF4AA] text-[#1BF4AA] bg-[#1BF4AA]/10',
  },
  injection: {
    value: 'injection',
    label: 'Command Hijack Intrusion - Unauthorized Signature Injection',
    subsystem: 'Comms',
    faultType: 'Malicious Signature Injection Attempt',
    register: '0xSIG_RETRY_BUF (CY-1 spoof lockout triggered)',
    score: 99.1,
    indicator: 'ATTACK',
    indicatorText: 'QUANTUM SPOOF EXPLOIT CRITICAL',
    badgeColor: 'border-[#FF3B30] text-[#FF3B30] bg-[#FF3B30]/10 animate-pulse',
  },
  battery_fail: {
    value: 'battery_fail',
    label: 'Inject Battery Failure - Shunt Circuit Overload',
    subsystem: 'Power',
    faultType: 'Thermal Runaway Run/Shunt Malfunction',
    register: '0xPWR_BAT_SNT (Voltage shunt drop)',
    score: 94.2,
    indicator: 'NATURAL',
    indicatorText: 'BATTERY CELL INSTABILITY',
    badgeColor: 'border-red-400 text-red-400 bg-red-400/10 animate-pulse',
  },
  adcs_fail: {
    value: 'adcs_fail',
    label: 'Inject ADCS Failure - Hardware Torque Coil Saturation',
    subsystem: 'ADCS',
    faultType: 'Attitude Magnetic Torquer Coil Fault',
    register: '0xADCS_COIL_SAT (Magnetic flux lockout)',
    score: 97.8,
    indicator: 'NATURAL',
    indicatorText: 'ADCS ACTUATOR SATURATION',
    badgeColor: 'border-red-400 text-red-400 bg-red-400/10 animate-pulse',
  }
};

export default function OperatorControlPanel() {
  const [selectedFault, setSelectedFault] = useState<'seu' | 'leak' | 'injection' | 'battery_fail' | 'adcs_fail'>('seu');
  const [activeDiagnostic, setActiveDiagnostic] = useState<FaultOption>(FAULT_DATABASE.seu);
  const [injecting, setInjecting] = useState(false);
  const [isUplinking, setIsUplinking] = useState(false);
  const [uplinkProgress, setUplinkProgress] = useState(0);
  const [uplinkMessage, setUplinkMessage] = useState('');
  const [recovered, setRecovered] = useState(false);
  const [countdown, setCountdown] = useState(42);
  const [systemUptime, setSystemUptime] = useState('04d : 12h : 33m : 42s');

  // Interactive trace updates
  const [traceLogs, setTraceLogs] = useState<string[]>([
    '[agent:ingest] Satellite LEO telemetry parsed successfully. Ground range: 541 KM.',
    '[agent:classify] Analyzing ADCS Pitch Euler drift curve parameters. Cross-entropy check complete.',
    '[agent:diagnose] Identified gyroscopic torque variation event. Confidence 96.4%.',
    '[agent:security] Verifying Lattice signatures... Status: STANDBY (Uplink Ready).'
  ]);

  // Confusion matrix simulated values
  const [confusionVals, setConfusionVals] = useState({
    truePos: 98.2,
    falsePos: 0.8,
    trueNeg: 99.1,
    falseNeg: 0.9,
    accuracy: 98.8
  });

  // Rogue alerts CY-1 feed
  const [rogueAlerts, setRogueAlerts] = useState<Array<{ id: string; time: string; msg: string; type: 'nominal' | 'alert' }>>([
    { id: '1', time: '14:02:18', msg: 'CY-1 Rogue Detector: No unauthorized 9.68GHz carriers present.', type: 'nominal' },
    { id: '2', time: '14:08:44', msg: 'CY-1: Telemetry envelope signatures verified with high-entropy lattice seed.', type: 'nominal' },
  ]);

  // Time stamp interval updates
  useEffect(() => {
    const timer = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 1) return 45;
        return prev - 1;
      });

      // Fluctuate confusion accuracy subtly
      setConfusionVals(prev => {
        const dev = (Math.random() - 0.5) * 0.1;
        return {
          ...prev,
          truePos: Number(Math.min(100, Math.max(90, prev.truePos + dev)).toFixed(1)),
          accuracy: Number(Math.min(100, Math.max(95, prev.accuracy + dev / 2)).toFixed(1))
        };
      });

      // Update uptime representation seconds
      const now = new Date();
      const s = now.getSeconds().toString().padStart(2, '0');
      const m = now.getMinutes().toString().padStart(2, '0');
      setSystemUptime(`04d : 12h : ${m}m : ${s}s`);
    }, 1000);

    return () => clearInterval(timer);
  }, []);

  const handleInjectFault = () => {
    setInjecting(true);
    setRecovered(false);
    const target = FAULT_DATABASE[selectedFault];
    setActiveDiagnostic(target);

    // Dynamic traces updating based on fault option
    if (selectedFault === 'seu') {
      setTraceLogs([
        `[agent:ingest] Real-time LEO telemetry registered high ADCS torque spike.`,
        `[agent:classify] Evaluating vector parameters: [pitch=${(3.5 + Math.random()).toFixed(2)} deg/s, yaw_delta=2.11].`,
        `[agent:diagnose] Classified gyroscopic register bit flip. Slew control compromised.`,
        `[agent:security] Security integrity check nominal. No rogue access detected.`,
        `[agent:recovery] Recovery graph initialized. Ready for Dilithium secure command uplink sequence.`
      ]);
      setRogueAlerts(prev => [
        { id: Date.now().toString(), time: new Date().toLocaleTimeString(), msg: 'CY-1 Scope: Ion collision telemetry signature detected. Zero network intrusion.', type: 'nominal' },
        ...prev.slice(0, 3)
      ]);
    } else if (selectedFault === 'leak') {
      setTraceLogs([
        `[agent:ingest] OBC Core processing thread registry warning. Register Stack limit exceeded.`,
        `[agent:classify] Sub-thread overflow check. Matched pattern [OBC_MEM_OVERRUN_v4].`,
        `[agent:diagnose] Detected unreleased telemetry loop leak in buffer thread. Stack Pointer shifting.`,
        `[agent:recovery] Recovery compiled. Preparing stack pointer flush cycle command payload.`
      ]);
      setRogueAlerts(prev => [
        { id: Date.now().toString(), time: new Date().toLocaleTimeString(), msg: 'CY-1 Scope: Buffer overflow identified. Signature origin: Internal Thread pool.', type: 'nominal' },
        ...prev.slice(0, 3)
      ]);
    } else if (selectedFault === 'injection') {
      setTraceLogs([
        `[agent:ingest] Comms transceiver alert: CRITICAL. Signature mismatch index.`,
        `[agent:classify] Intercepted unauthorized command ledger packet #CY-RETRY.`,
        `[agent:diagnose] ALERT: Adversarial signature injection detected (RSA key collision attempt).`,
        `[agent:security] Command Rejected. Initiating rogue lockout protocol and signature rotation trace.`,
        `[agent:recovery] Post-quantum emergency rotation pipeline established. Awaiting pilot authentication lock.`
      ]);
      setRogueAlerts(prev => [
        { id: Date.now().toString(), time: new Date().toLocaleTimeString(), msg: 'CY-1 LOCKOUT EVENT: Multiple unauthorized packet pre-stamps rejected on Ku-band legacy port.', type: 'alert' },
        ...prev.slice(0, 3)
      ]);
    } else if (selectedFault === 'battery_fail') {
      setTraceLogs([
        `[agent:ingest] POWER board warning: High shunt current draw on cell matrix 4B.`,
        `[agent:classify] Cell telemetry analyzer: mismatch between charge array and discharge voltage.`,
        `[agent:diagnose] Identified Power Thermal Runaway risk in shunt circuit configuration. Shunt current: 12.1A.`,
        `[agent:recovery] Creating bypass script: isolator circuit close & cell re-route instructions.`
      ]);
      setRogueAlerts(prev => [
        { id: Date.now().toString(), time: new Date().toLocaleTimeString(), msg: 'CY-1 Scope: Shunt circuit heat fluctuation alert. Stabilizers ready.', type: 'nominal' },
        ...prev.slice(0, 3)
      ]);
    } else if (selectedFault === 'adcs_fail') {
      setTraceLogs([
        `[agent:ingest] ADCS actuator feedback: magnetic torquer control coil saturated.`,
        `[agent:classify] Torquer feedback torque model vs command mismatched by 150mN-m.`,
        `[agent:diagnose] Hard lockout in pitch coil active. Satellite drift rotation rate increasing.`,
        `[agent:recovery] Demagnetization sequence compiled. Command payload scheduled.`
      ]);
      setRogueAlerts(prev => [
        { id: Date.now().toString(), time: new Date().toLocaleTimeString(), msg: 'CY-1 Scope: Torquer lock saturation detected. Attitude safety hold ready.', type: 'nominal' },
        ...prev.slice(0, 3)
      ]);
    }

    // Trigger CustomEvent to cause visual telemetry spikes in SatelliteDashboard.tsx!
    const customEvent = new CustomEvent('inject-satellite-fault', {
      detail: { fault: selectedFault }
    });
    window.dispatchEvent(customEvent);

    setTimeout(() => {
      setInjecting(false);
    }, 800);
  };

  const handleAuthoriseUplink = () => {
    if (isUplinking) return;
    setIsUplinking(true);
    setUplinkProgress(0);
    setRecovered(false);

    const steps = [
      'MODULATING GROUND TRANSMITTER ENVELOPE (9.68 GHz)...',
      'ENCRYPTING SECURE COMMAND LEDGER WITH CRYSTALS-DILITHIUM...',
      'BROADCASTING HARDENED LATTICE SIGNATURES...',
      'VERIFYING SATELLITE ACC-CMD HANDSHAKE PACKET...',
      'STATE RETRIEVAL COMPLETE. SATELLITE CORE STABILIZED!'
    ];

    let currentStep = 0;
    const interval = setInterval(() => {
      setUplinkProgress(prev => {
        if (prev >= 100) {
          clearInterval(interval);
          setIsUplinking(false);
          setRecovered(true);
          setTraceLogs(prevLogs => [
            ...prevLogs,
            '[agent:uplink] SUCCESS: Core convergence telemetry recovered. Stabilizer active.',
            '[agent:telemetry] Normal telemetry streaming resumed. Mission nominal.'
          ]);
          return 100;
        }
        
        // Progress incremental update
        const nextProg = prev + 5;
        const stepIdx = Math.floor((nextProg / 100) * steps.length);
        if (stepIdx < steps.length && stepIdx !== currentStep) {
          currentStep = stepIdx;
          setUplinkMessage(steps[stepIdx]);
        }
        return nextProg;
      });
    }, 150);
  };

  return (
    <div className="flex-1 flex flex-col gap-6 font-sans text-[#D4D4D4] select-text">
      
      {/* HUD Header Bar */}
      <div className="bg-[#1A1A1A]/95 border border-white/10 p-4 rounded-sm shadow-xl relative overflow-hidden flex flex-wrap items-center justify-between gap-4">
        {/* Corner highlights */}
        <div className="absolute top-0 left-0 w-2 h-2 border-t border-l border-signal-green"></div>
        <div className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-signal-green"></div>

        <div className="flex items-center gap-3">
          <span className="material-symbols-outlined text-signal-green text-sm animate-pulse">terminal</span>
          <div>
            <h1 className="font-display text-base font-black text-white uppercase tracking-tight leading-none">
              MISSION CONTROL SECURITY & RECOVERY CENTER
            </h1>
            <p className="font-mono text-[9px] text-[#D4D4D4]/50 uppercase tracking-widest mt-1">
              OPERATOR STATION: OP-HQ_DELHI • REAL-TIME HARD RESCUE TERMINAL
            </p>
          </div>
        </div>

        <div className="flex gap-4 font-mono text-[10.5px]">
          <div>
            SYSTEM_UPTIME: <span className="text-white font-bold">{systemUptime}</span>
          </div>
          <div>
            CRYPTO: <span className="text-signal-green font-bold">LATTICE_OK</span>
          </div>
        </div>
      </div>

      {/* Grid: Fault Injection Demo Control & Diagnosis Matrix Panels */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        {/* COLUMN LEFT: Fault Injection & Diagnosis Panel (7 Cols) */}
        <div className="lg:col-span-7 flex flex-col gap-6">

          {/* Panel A: Fault Diagnosis Panel (AI-1 Outputs) */}
          <div className="bg-[#1A1A1A]/95 border border-white/10 p-5 rounded-sm shadow-md flex flex-col relative">
            <div className="flex items-center justify-between border-b border-white/10 pb-3 mb-4">
              <h2 className="font-display text-xs font-black text-white uppercase tracking-wider flex items-center gap-1.5">
                <Cpu className="w-4 h-4 text-[#1BF4AA]" />
                FAULT CLASSIFICATION ANALYSIS (MODULE: AI-1)
              </h2>
              <span className={`text-[9.5px] font-mono px-2 py-0.5 border rounded-sm font-bold uppercase ${activeDiagnostic.badgeColor}`}>
                {activeDiagnostic.indicatorText}
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
              {/* Classification specs left */}
              <div className="space-y-3 font-mono text-xs">
                <div className="bg-[#0D0D0D] p-3 border border-white/5 rounded-sm">
                  <div className="text-[10px] text-[#D4D4D4]/50 uppercase">CLASSIFIED FAULT TYPE</div>
                  <div className="text-white font-black mt-1 uppercase text-[12.5px] leading-tight">
                    {activeDiagnostic.faultType}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="bg-[#0D0D0D] p-3 border border-white/5 rounded-sm">
                    <div className="text-[10px] text-[#D4D4D4]/50 uppercase">TARGET PORT</div>
                    <div className="text-white font-bold mt-1">
                      {activeDiagnostic.subsystem} BUS
                    </div>
                  </div>
                  <div className="bg-[#0D0D0D] p-3 border border-white/5 rounded-sm">
                    <div className="text-[10px] text-[#D4D4D4]/50 uppercase">CONFIDENCE</div>
                    <div className="text-signal-green font-black mt-1">
                      {activeDiagnostic.score.toFixed(1)}%
                    </div>
                  </div>
                </div>

                <div className="bg-[#0D0D0D] p-3 border border-white/5 rounded-sm">
                  <div className="text-[10px] text-[#D4D4D4]/50 uppercase">IMPACTED VECTOR REGISTER REFERENCE</div>
                  <div className="text-[#4fc3f7] font-bold mt-0.5 truncate select-all">
                    {activeDiagnostic.register}
                  </div>
                </div>
              </div>

              {/* Live Confusion Matrix (Classifier accuracy) */}
              <div className="bg-[#0D0D0D] border border-white/10 p-3 rounded-sm flex flex-col font-mono text-[10px]">
                <div className="text-white font-bold uppercase tracking-wider border-b border-white/5 pb-1.5 mb-2 flex justify-between items-center">
                  <span>LIVE CLASSIFICATION ACCURACY</span>
                  <span className="text-[#1BF4AA]">{confusionVals.accuracy}%</span>
                </div>
                
                <div className="grid grid-cols-3 gap-1.5 text-center mt-1">
                  <div></div>
                  <div className="text-[#D4D4D4]/55 uppercase text-[8px] font-bold grid-column-span-2">PREDICTED</div>
                  <div></div>

                  {/* Labels row */}
                  <div className="text-left text-[#D4D4D4]/55 uppercase text-[8px] font-bold flex items-center">ACTUAL</div>
                  <div className="bg-signal-green/10 text-signal-green p-1.5 border border-signal-green/20 rounded-sm">
                    <div className="text-[8px] text-[#D4D4D4]/50 font-normal">TRUE NOM</div>
                    <div className="font-bold">{confusionVals.truePos}%</div>
                  </div>
                  <div className="bg-red-500/5 text-red-400 p-1.5 border border-red-500/10 rounded-sm">
                    <div className="text-[8px] text-[#D4D4D4]/50 font-normal">FALSE FAULT</div>
                    <div className="font-bold">{confusionVals.falsePos}%</div>
                  </div>

                  {/* Sec row */}
                  <div className="text-left"></div>
                  <div className="bg-red-500/5 text-red-400 p-1.5 border border-red-500/10 rounded-sm">
                    <div className="text-[8px] text-[#D4D4D4]/50 font-normal">FALSE NOM</div>
                    <div className="font-bold">{confusionVals.falseNeg}%</div>
                  </div>
                  <div className="bg-signal-green/10 text-signal-green p-1.5 border border-signal-green/20 rounded-sm">
                    <div className="text-[8px] text-[#D4D4D4]/50 font-normal">TRUE FAULT</div>
                    <div className="font-bold">{confusionVals.trueNeg}%</div>
                  </div>
                </div>

                <div className="mt-3 text-[9px] text-[#D4D4D4]/40 leading-tight">
                  CLASSIFIER CONVERGENCE: LSTM neural array training metrics updated dynamically every 1s.
                </div>
              </div>
            </div>
          </div>

          {/* Panel B: Fault Injection UI (Demo control) */}
          <div className="bg-[#1A1A1A]/95 border border-red-500/15 p-5 rounded-sm shadow-md flex flex-col relative relative select-none">
            {/* Top warning ribbon */}
            <div className="absolute top-0 inset-x-0 h-1 bg-gradient-to-r from-red-600/60 via-red-500/10 to-red-600/60"></div>
            
            <div className="flex items-center justify-between border-b border-[#FF3B30]/20 pb-2.5 mb-4">
              <h3 className="font-display text-xs font-black text-red-400 uppercase tracking-wider flex items-center gap-1.5">
                <Warning className="w-4 h-4 text-red-500 animate-pulse" />
                SATELLITE DEMO FAULT INJECTION CONTROLS
              </h3>
              <span className="text-[9px] font-mono text-red-500 font-bold uppercase tracking-widest animate-pulse">
                JUDGES TRIGGER AREA
              </span>
            </div>

            <p className="text-xs text-[#D4D4D4]/75 mb-4 leading-normal font-sans">
              Selecting anomalies below and initiating injection directly affects the telemetry telemetry lanes stream. You can verify instant telemetry spikes on the main LEO dashboard telemetry charts after clicking.
            </p>

            <div className="flex flex-col sm:flex-row gap-4 items-stretch sm:items-center">
              <div className="flex-1">
                <label className="block text-[10px] font-mono text-[#D4D4D4]/60 uppercase mb-1.5 tracking-wider font-bold">Anomaly Scenario Model</label>
                <select
                  value={selectedFault}
                  onChange={e => setSelectedFault(e.target.value as any)}
                  className="w-full bg-[#0D0D0D] border border-[#FF3B30]/30 hover:border-red-500 text-white font-mono text-xs p-3.5 rounded-sm focus:outline-none focus:border-red-500"
                >
                  <option value="seu">SINGLE EVENT UPSET (SEU) - GYRO TORQUE BIT REVERSAL</option>
                  <option value="leak">SOFTWARE MEMORY LEAK - PROCESS THREAD RUNOVER</option>
                  <option value="injection">COMMAND INTRUSION - UNAUTHORIZED SIGNATURE ATTACK</option>
                  <option value="battery_fail">BATTERY SHUNT SCHEMATIC FAULT - THERMAL OVERLOAD</option>
                  <option value="adcs_fail">HARD ADCS TORQUER COIL FAILURE - TORQUE LOCK</option>
                </select>
              </div>

              <div className="flex sm:pt-5 select-none">
                <button
                  type="button"
                  onClick={handleInjectFault}
                  disabled={injecting}
                  className="bg-red-600 hover:bg-red-500 text-white font-mono font-black text-xs px-6 py-3.5 border border-transparent hover:border-red-400 rounded-sm shadow-lg shadow-red-600/20 active:scale-95 transition-all w-full cursor-pointer uppercase tracking-widest"
                >
                  {injecting ? 'INJECTING FAULT...' : 'INJECT SATELLITE FAULT'}
                </button>
              </div>
            </div>
          </div>

        </div>

        {/* COLUMN RIGHT: Recovery Plan & Authorisation (5 Cols) */}
        <div className="lg:col-span-5 flex flex-col gap-6">

          {/* Panel C: Recovery Plan Display */}
          <div className="bg-[#1A1A1A]/95 border border-white/10 p-5 rounded-sm shadow-md flex flex-col h-full select-text">
            <h3 className="font-display text-xs font-black text-white uppercase tracking-wider border-b border-white/10 pb-3 mb-4 flex items-center gap-1.5">
              <span className="material-symbols-outlined text-[#1BF4AA] text-sm font-bold">healing</span>
              AI-2 RECOVERY RESOLVER PLAN
            </h3>

            {/* Generated sequence */}
            <div className="font-mono text-xs space-y-2 mb-4 bg-[#0D0D0D] p-3 border border-white/5 rounded-sm">
              <div className="text-[10px] tracking-wider text-[#D4D4D4]/50 uppercase border-b border-white/5 pb-1.5 mb-1.5 font-bold">
                GENERATED UPLINK COMMAND PLAN (HUMAN-READABLE)
              </div>
              <div className="flex items-start gap-2 text-[11px] leading-tight text-white py-0.5">
                <span className="text-[#1BF4AA] font-bold">[1]</span>
                <span className="flex-1">SYS_DECRYPT_RING_LOCK (Modulates core carrier to sync Ku ground window)</span>
                <span className="text-[#D4D4D4]/40 text-[9.5px]">200ms</span>
              </div>
              <div className="flex items-start gap-2 text-[11px] leading-tight text-white py-0.5">
                <span className="text-[#1BF4AA] font-bold">[2]</span>
                <span className="flex-1">STABILIZE_ADCS_TORQ_COILS (Fires magnetic torquers to lock roll/pitch delta)</span>
                <span className="text-[#D4D4D4]/40 text-[9.5px]">800ms</span>
              </div>
              <div className="flex items-start gap-2 text-[11px] leading-tight text-white py-0.5">
                <span className="text-[#1BF4AA] font-bold">[3]</span>
                <span className="flex-1">COMPUTE_LATTICE_INTEGRITY (Injects high-entropy PQC signature keys)</span>
                <span className="text-[#D4D4D4]/40 text-[9.5px]">1.2s</span>
              </div>
              <div className="flex items-start gap-2 text-[11px] leading-tight text-white py-0.5">
                <span className="text-[#1BF4AA] font-bold">[4]</span>
                <span className="flex-1">SYS_OBC_REG_FLUSH_WARN (Resets bit flips on diagnostic register spaces)</span>
                <span className="text-[#D4D4D4]/40 text-[9.5px]">400ms</span>
              </div>
            </div>

            {/* Agent LangGraph Reasoning Trace */}
            <div className="flex-grow flex flex-col font-mono text-[10.5px]">
              <div className="text-[9.5px] font-bold uppercase tracking-wider text-[#D4D4D4]/50 mb-1.5">
                LangGraph Multi-Agent Trace logs
              </div>
              <div className="flex-grow bg-[#090B10] border border-white/5 p-3 rounded-sm space-y-2 max-h-36 overflow-y-auto tech-scrollbar">
                {traceLogs.map((log, idx) => (
                  <div key={idx} className="leading-relaxed hover:text-white transition-colors">
                    <span className="text-signal-green font-bold">&gt;&gt;</span> {log}
                  </div>
                ))}
              </div>
            </div>

            {/* AUTHORISE BUTTON AREA */}
            <div className="mt-5 pt-4 border-t border-white/10 flex flex-col gap-3">
              <div className="flex justify-between items-center font-mono text-[10.5px]">
                <span className="text-[#D4D4D4]/50">Next Ahmedabad Pass Window:</span>
                <span className="text-amber-400 font-bold animate-pulse">00:{countdown.toString().padStart(2, '0')}</span>
              </div>

              {isUplinking ? (
                <div className="bg-[#0D0D0D] border border-[#1BF4AA]/30 p-3 rounded-sm space-y-2">
                  <div className="flex justify-between font-mono text-[9.5px] font-bold text-[#1BF4AA]">
                    <span className="animate-pulse">{uplinkMessage}</span>
                    <span>{uplinkProgress}%</span>
                  </div>
                  <div className="w-full bg-white/5 h-1.5 rounded-full overflow-hidden">
                    <div 
                      className="bg-[#1BF4AA] h-full rounded-full transition-all duration-150 shadow-[0_0_8px_rgba(27,244,170,0.6)]" 
                      style={{ width: `${uplinkProgress}%` }}
                    />
                  </div>
                </div>
              ) : recovered ? (
                <div className="bg-signal-green/10 border border-signal-green p-3 rounded-sm flex flex-col items-center justify-center text-center relative overflow-hidden glow-primary animate-[fade-in_0.5s_ease-out]">
                  <div className="absolute top-0 inset-x-0 h-0.5 bg-white"></div>
                  <Check className="w-6 h-6 text-signal-green mb-1 animate-bounce" />
                  <div className="font-mono text-xs font-black text-white uppercase tracking-wider">
                    SATELLITE RECOVERED SUCCESSFULLY
                  </div>
                  <div className="font-mono text-[10px] text-signal-green tracking-tight uppercase font-bold mt-1">
                    SYS_CONVERGENCE REALIGN NOMINAL PRE-SIGN OK
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={handleAuthoriseUplink}
                  className="bg-signal-green hover:bg-[#D4FF00] text-black font-mono font-black text-xs px-5 py-4 rounded-sm border border-transparent shadow-[0_0_15px_rgba(204,255,0,0.25)] hover:shadow-[0_0_25px_rgba(204,255,0,0.4)] transition-all cursor-pointer uppercase tracking-widest flex items-center justify-center gap-2"
                >
                  <Radio className="w-4 h-4 animate-ping" />
                  AUTHORISE AI-2 RECOVERY UPLINK
                </button>
              )}
            </div>
          </div>

        </div>

      </div>

      {/* Panel D: Post-Quantum Crypto Verification Ledger Panel */}
      <div className="bg-[#1A1A1A]/95 border border-white/10 p-5 rounded-sm shadow-md flex flex-col relative">
        <h3 className="font-display text-xs font-black text-white uppercase tracking-wider border-b border-white/10 pb-3 mb-4 flex items-center gap-1.5">
          <Shield className="w-4 h-4 text-signal-green" />
          POST-QUANTUM CRYPTO SIGNATURE & ROGUE VERIFICATION PANEL
        </h3>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 font-mono text-xs">
          
          {/* Box 1: Signature Hash Representation */}
          <div className="bg-[#0D0D0D] border border-white/5 p-3 rounded-sm flex flex-col justify-between">
            <div>
              <div className="text-[10px] text-[#D4D4D4]/50 uppercase font-bold tracking-wider mb-2">ACTIVE COMMISSION SIGNATURE</div>
              <div className="bg-[#090B10] p-2 border border-white/5 font-mono text-[9px] text-[#1BF4AA] break-all select-all select-text font-bold leading-relaxed rounded-sm">
                0x9A4BEFB79AD204F2EAF1D304D1BFEA93F238AEFE2B1A8CD7BBE41249FFFF9CBA82301A987D
              </div>
            </div>
            <div className="mt-3 text-[9px] text-[#D4D4D4]/40">
              CRYSTALS-Dilithium3 algorithm generating asymmetric signatures via high-dimension polynomial rings.
            </div>
          </div>

          {/* Box 2: Secure Ledger Transaction details */}
          <div className="bg-[#0D0D0D] border border-white/5 p-3 rounded-sm flex flex-col">
            <div className="text-[10px] text-[#D4D4D4]/50 uppercase font-bold tracking-wider mb-2">BLOCK LEDGER ENTRY RECORD</div>
            <div className="space-y-1.5 text-[10.5px]">
              <div className="flex justify-between">
                <span className="text-[#D4D4D4]/50">ENTRY_ID:</span>
                <span className="text-white font-bold">#LEDG-99841</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#D4D4D4]/50">TIMESTAMP:</span>
                <span className="text-white">13-JUN-2026 UTC</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#D4D4D4]/50">NONCE:</span>
                <span className="text-[#4fc3f7] select-all">0x7F2A01EF8C</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#D4D4D4]/50">LEDGER STATUS:</span>
                <span className="text-signal-green font-bold uppercase tracking-wider font-extrabold flex items-center gap-1">
                  <span className="w-1.5 h-1.5 bg-signal-green rounded-full"></span>COMMITTED
                </span>
              </div>
            </div>
          </div>

          {/* Box 3: Quantum Threat Matrix breakdown */}
          <div className="bg-[#0D0D0D] border border-white/5 p-3 rounded-sm flex flex-col justify-between">
            <div className="text-[10px] text-[#D4D4D4]/50 uppercase font-bold tracking-wider mb-2">QUANTUM INTERCEPT THREAT MATRIX</div>
            <div className="space-y-1.5 text-[10px]">
              <div className="flex justify-between border-b border-white/5 pb-1 text-red-400 font-bold">
                <span>Legacy RSA-2048:</span>
                <span>CRACKED in ~142s</span>
              </div>
              <div className="flex justify-between border-b border-white/5 pb-1 text-signal-green font-bold">
                <span>Dilithium3 Sec Level:</span>
                <span>SECURE &gt;1.5x10^29 yr</span>
              </div>
              <div className="text-[8.5px] text-[#D4D4D4]/40 mt-1 leading-normal uppercase">
                *ESTIMATED SPECS ASSUMING A STATE LEVEL 2048-QUBIT QUANTUM COHERENT COMPUTER SHOR ATACK INDEX RANGE.
              </div>
            </div>
          </div>

        </div>

        {/* Dynamic Alert feed from CY-1 Rogue detector */}
        <div className="mt-4 pt-3.5 border-t border-white/5 font-mono text-[10.5px]">
          <div className="text-[9.5px] font-bold text-red-400 uppercase tracking-wider mb-1.5 flex items-center gap-1">
            <span className="inline-flex w-1.5 h-1.5 bg-red-400 animate-ping rounded-full"></span>
            CY-1 SATELLITE CO-ORDINATION ROGUE CARRIER WATCH
          </div>
          <div className="space-y-1 bg-[#090b0d] p-2.5 rounded-sm border border-white/5 max-h-24 overflow-y-auto tech-scrollbar">
            {rogueAlerts.map(alert => (
              <div 
                key={alert.id}
                className={`py-0.5 border-b border-white/5 last:border-0 flex items-start gap-1 ${
                  alert.type === 'alert' ? 'text-red-400 font-bold' : 'text-[#D4D4D4]/75'
                }`}
              >
                <span className="text-[#D4D4D4]/40 font-bold">[{alert.time}]</span>
                <span className="flex-1">{alert.msg}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

    </div>
  );
}
