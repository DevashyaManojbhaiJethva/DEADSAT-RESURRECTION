import { useState, useEffect } from 'react';
import { TelemetryState, SystemLog, CopilotMessage } from '../types';
import { Radio, Warning, Activity, Shield } from './Icons';
import EarthGlobe from './EarthGlobe';

interface TelemetryConsoleProps {
  telemetry: TelemetryState;
  onPing: () => void;
  logs: SystemLog[];
  copilotMessages: CopilotMessage[];
}

export default function TelemetryConsole({ telemetry, onPing, logs, copilotMessages }: TelemetryConsoleProps) {
  const [pingPulse, setPingPulse] = useState(false);
  const [warningsEnabled, setWarningsEnabled] = useState(true);

  const handlePingPress = () => {
    setPingPulse(true);
    onPing();
    setTimeout(() => {
      setPingPulse(false);
    }, 1500);
  };

  return (
    <div className="flex-1 flex flex-col xl:flex-row gap-6 min-w-0 font-sans">
      {/* Central Interactive Satellite Tracker / Grid HUD */}
      <div className="flex-1 flex flex-col gap-6 min-w-0">
        
        {/* Globe/LEO Tracking view with EarthGlobe Backdrop */}
        <div className="h-96 bg-[#1A1A1A]/95 border border-signal-green/20 relative overflow-hidden rounded-sm flex flex-col shadow-lg">
          {/* EarthGlobe shared 3D Earth component */}
          <div className="absolute inset-0 z-0">
            <EarthGlobe />
          </div>

          <div className="absolute top-4 left-4 z-20 flex flex-wrap gap-2 pointer-events-none">
            <span className="bg-signal-green/10 text-signal-green font-mono text-[10px] px-2 py-1 border border-signal-green/30 rounded-sm font-bold tracking-wider">
              ORBIT: LEO (LOW EARTH ORBIT)
            </span>
            <span className="bg-[#2D2D2D]/90 backdrop-blur-xs text-white font-mono text-[10px] px-2 py-1 rounded-sm border border-white/10">
              ALTITUDE: {telemetry.altitude.toFixed(2)} KM
            </span>
            <span className="bg-[#2D2D2D]/90 backdrop-blur-xs text-white font-mono text-[10px] px-2 py-1 rounded-sm border border-white/10">
              VELOCITY: {telemetry.velocity.toFixed(3)} KM/S
            </span>
          </div>

          <div className="absolute top-4 right-4 z-20 flex gap-2">
            <button 
              onClick={handlePingPress}
              disabled={pingPulse}
              className={`font-mono text-[10px] px-3 py-1 font-bold tracking-wider rounded-sm cursor-pointer transition-all ${
                pingPulse
                  ? 'bg-signal-green text-black shadow-[0_0_15px_rgba(204,255,0,0.4)] pointer-events-none'
                  : 'bg-[#2D2D2D]/90 backdrop-blur-xs text-signal-green border border-signal-green/20 hover:bg-signal-green/10 active:scale-95'
              }`}
            >
              {pingPulse ? "PING SIGNAL SENT..." : "PING SATELLITE"}
            </button>
          </div>

          {/* Graphic HUD Display Plane */}
          <div className="flex-1 relative flex items-center justify-center bg-transparent pointer-events-none z-10">
            <div className={`absolute w-32 h-32 rounded-full border border-signal-green/10 ${pingPulse ? 'animate-ping border-signal-green/40' : ''}`}></div>
            <div className="absolute w-64 h-64 border border-dashed border-[#4fc3f7]/20 rounded-full animate-[spin_100s_linear_infinite]"></div>

            {/* Simulated Satellite Vector Alignment Ring */}
            <div className="absolute w-56 h-56 border-2 border-signal-green/10 rounded-full flex items-center justify-center">
              <div className="absolute top-0 left-1/2 -translate-x-1/2 w-4 h-[2px] bg-signal-green"></div>
              <div className="absolute bottom-0 left-1/2 -translate-x-1/2 w-4 h-[2px] bg-signal-green"></div>
              <div className="absolute left-0 top-1/2 -translate-y-1/2 h-4 w-[2px] bg-signal-green"></div>
              <div className="absolute right-0 top-1/2 -translate-y-1/2 h-4 w-[2px] bg-signal-green"></div>
            </div>

            {/* SVG satellite node mapping- flat circle removed, rest of HUD graphics matched */}
            <svg viewBox="0 0 400 400" className="w-full h-full max-w-sm relative z-10 select-none">
              {/* Vector scope reticles */}
              <circle cx="200" cy="200" r="110" fill="none" stroke="#CCFF00" strokeWidth="0.5" strokeOpacity="0.2" strokeDasharray="3 3"/>
              
              {/* Dynamic satellite representation with crosshair */}
              <circle cx="200" cy="90" r="5" fill="#CCFF00" className="animate-ping" />
              <circle cx="200" cy="90" r="3" fill="#CCFF00" />
              
              {/* Scope labels */}
              <text x="210" y="85" fill="#CCFF00" fontSize="9" fontFamily="monospace">CARTOSAT-3</text>
              <text x="210" y="97" fill="#D4D4D4" fontSize="8" fontFamily="monospace" fillOpacity="0.8">RNG_LOCK_OK</text>
              
              {/* Scope Crosshair lines */}
              <line x1="200" y1="80" x2="200" y2="100" stroke="#CCFF00" strokeWidth="0.5" />
              <line x1="190" y1="90" x2="210" y2="90" stroke="#CCFF00" strokeWidth="0.5" />
            </svg>

            {/* Floating Coordinate Tags */}
            <div className="absolute bottom-4 left-4 font-mono text-[10px] text-[#D4D4D4]/60 space-y-1 bg-[#0D0D0D]/90 p-2.5 border border-white/10 rounded-sm pointer-events-auto">
              <div>LATITUDE: <span className="text-[#4fc3f7]">{telemetry.lat.toFixed(4)}° N</span></div>
              <div>LONGITUDE: <span className="text-[#4fc3f7]">{telemetry.lng.toFixed(4)}° E</span></div>
              <div>TEMPERATURE: <span className="text-white">{telemetry.temperature.toFixed(1)} K</span></div>
            </div>

            <div className="absolute bottom-4 right-4 font-mono text-[10px] text-[#D4D4D4]/60 space-y-1 bg-[#0D0D0D]/90 p-2.5 border border-white/10 rounded-sm pointer-events-auto">
              <div className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 bg-signal-green rounded-full"></span>
                <span>COMMS_SDR: ONLINE</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 bg-signal-green rounded-full"></span>
                <span>DILITHIUM_A3: ACTIVE</span>
              </div>
            </div>
          </div>
        </div>

        {/* Telemetry charts grids */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          
          {/* Chart 1: Power array */}
          <div className="bg-[#1A1A1A] border border-white/10 p-4 rounded-sm flex flex-col relative h-48">
            <div className="flex justify-between items-center border-b border-white/10 pb-2 mb-3">
              <span className="font-mono text-[11px] font-bold text-white uppercase tracking-wider">POWER ARRAY OUTPUT</span>
              <span className="font-mono text-signal-green font-bold text-sm tracking-widest">{telemetry.powerArray.toFixed(1)}%</span>
            </div>

            {/* Horizontal or Vertical bars */}
            <div className="flex-1 flex items-end gap-1.5 pb-2">
              <div className="w-full bg-signal-green/20 h-[55%] rounded-sm"></div>
              <div className="w-full bg-signal-green/25 h-[62%] rounded-sm"></div>
              <div className="w-full bg-signal-green/30 h-[70%] rounded-sm"></div>
              <div className="w-full bg-signal-green/45 h-[68%] rounded-sm"></div>
              <div className="w-full bg-signal-green/60 h-[75%] rounded-sm"></div>
              <div className="w-full bg-signal-green/80 h-[80%] rounded-sm"></div>
              <div className="w-full bg-signal-green h-[84.2%] rounded-sm relative glow-primary">
                {/* Active bar highlight indicator */}
                <span className="absolute top-0 inset-x-0 h-0.5 bg-white shadow-md"></span>
              </div>
            </div>
            <div className="flex justify-between font-mono text-[9px] text-[#D4D4D4]/55">
              <span>T-10m</span>
              <span>NOMINAL_COEFFICIENT</span>
              <span>NOW</span>
            </div>
          </div>

          {/* Chart 2: ADCS Stability */}
          <div className={`bg-[#1A1A1A] border p-4 rounded-sm flex flex-col relative h-48 transition-colors ${
            telemetry.adcsStability === 'NOMINAL' ? 'border-white/10' : 'border-threat-red/40 glow-error bg-threat-red/5'
          }`}>
            <div className="flex justify-between items-center border-b border-white/10 pb-2 mb-3">
              <span className={`font-mono text-[11px] font-bold uppercase tracking-wider ${
                telemetry.adcsStability === 'NOMINAL' ? 'text-white' : 'text-threat-red'
              }`}>ADCS ATTITUDE STABILITY</span>
              <span className={`font-mono font-bold text-sm tracking-widest ${
                telemetry.adcsStability === 'NOMINAL' ? 'text-[#FF3B30]' : 'text-threat-red animate-pulse'
              }`}>{telemetry.adcsStability}</span>
            </div>

            {/* Sine wave path indicator representing pitch delta */}
            <div className="flex-1 relative flex items-center justify-center opacity-85">
              <svg className="w-full h-full max-h-[100px] stroke-threat-red stroke-[2] fill-none" viewBox="0 0 300 100" preserveAspectRatio="none">
                <path d="M0 50 Q 30 10, 60 50 T 120 50 T 180 30 T 240 85 T 300 50" />
                {/* Visual grid lines behind */}
                <line x1="0" y1="50" x2="300" y2="50" stroke="#FF3B30" strokeWidth="0.5" strokeOpacity="0.2" strokeDasharray="4 4" />
              </svg>
            </div>
            <div className="flex justify-between font-mono text-[9px] text-[#D4D4D4]/55">
              <span className="text-[#FF3B30] font-bold">PITCH: {telemetry.adcsPitch.toFixed(2)}°</span>
              <span>SAMPLE_INTERVAL: 100ms</span>
              <span className="text-[#FF3B30] font-bold">YAW: {telemetry.adcsYaw.toFixed(2)}°</span>
            </div>
          </div>

          {/* Chart 3: Comms Bandwidth */}
          <div className="bg-[#1A1A1A] border border-white/10 p-4 rounded-sm flex flex-col relative h-48">
            <div className="flex justify-between items-center border-b border-white/10 pb-2 mb-3">
              <span className="font-mono text-[11px] font-bold text-white uppercase tracking-wider">COMMS DOWNLINK BANDWIDTH</span>
              <span className="font-mono text-data-blue font-bold text-sm tracking-widest">{telemetry.commsBandwidth.toFixed(2)} Gbps</span>
            </div>

            {/* Sparkline block telemetry */}
            <div className="flex-1 flex items-end gap-1.5 pb-2">
              <div className="flex-1 bg-data-blue/20 h-[15%] rounded-sm"></div>
              <div className="flex-1 bg-data-blue/30 h-[35%] rounded-sm"></div>
              <div className="flex-1 bg-data-blue/25 h-[28%] rounded-sm"></div>
              <div className="flex-1 bg-data-blue/45 h-[55%] rounded-sm"></div>
              <div className="flex-1 bg-data-blue/60 h-[48%] rounded-sm"></div>
              <div className="flex-1 bg-data-blue/70 h-[72%] rounded-sm"></div>
              <div className="flex-1 bg-data-blue h-[85%] rounded-sm glow-secondary"></div>
            </div>
            <div className="flex justify-between font-mono text-[9px] text-[#D4D4D4]/55">
              <span>Ku-BAND CARRIER</span>
              <span>FREQUENCY: 9.68 GHz</span>
              <span>STABLE</span>
            </div>
          </div>

          {/* Chart 4: OBC load */}
          <div className="bg-[#1A1A1A] border border-white/10 p-4 rounded-sm flex flex-col relative h-48">
            <div className="flex justify-between items-center border-b border-white/10 pb-2 mb-3">
              <span className="font-mono text-[11px] font-bold text-white uppercase tracking-wider">ONBOARD COMPUTER LOAD</span>
              <span className="font-mono text-signal-green font-bold text-sm tracking-widest">{telemetry.obcCpu}%</span>
            </div>

            {/* Core CPU / MEM progress columns */}
            <div className="flex-1 flex flex-col justify-center gap-4">
              <div className="space-y-1">
                <div className="flex justify-between text-[10px] font-mono text-[#D4D4D4]/60">
                  <span>CENTRAL CPU PROCESSING</span>
                  <span>{telemetry.obcCpu}%</span>
                </div>
                <div className="w-full bg-[#0D0D0D] h-3 rounded-sm p-[1px] border border-white/10">
                  <div className="h-full bg-signal-green transition-all duration-300" style={{ width: `${telemetry.obcCpu}%` }}></div>
                </div>
              </div>

              <div className="space-y-1">
                <div className="flex justify-between text-[10px] font-mono text-[#D4D4D4]/60">
                  <span>ONBOARD RAD-HARDENED RAM</span>
                  <span>{telemetry.obcMem}%</span>
                </div>
                <div className="w-full bg-[#0D0D0D] h-3 rounded-sm p-[1px] border border-white/10">
                  <div className="h-full bg-data-blue transition-all duration-300" style={{ width: `${telemetry.obcMem}%` }}></div>
                </div>
              </div>
            </div>
            
            <div className="flex justify-between font-mono text-[9px] text-[#D4D4D4]/55">
              <span>CORE_TEMPERATURE: NOMINAL</span>
              <span>OBC_BUILD: RISC-V HARSH</span>
            </div>
          </div>

        </div>
      </div>

      {/* Right Sidebar: AI Mission Copilot feed, System Logs, RF Waterfall Real-time canvas waterfall */}
      <div className="w-full xl:w-80 flex flex-col gap-6 shrink-0 font-mono">
        
        {/* AI Mission Copilot Feed */}
        <div className="bg-[#1A1A1A] border border-signal-green/20 p-4 rounded-sm flex flex-col h-56 shadow-md">
          <div className="text-[11px] font-bold text-white uppercase tracking-wider border-b border-white/10 pb-2 mb-3 flex items-center gap-2">
            <span className="material-symbols-outlined text-xs text-signal-green animate-pulse" style={{ fontVariationSettings: "'FILL' 1" }}>psychology</span>
            <span>AI MISSION COPILOT</span>
          </div>

          <div className="flex-1 text-[11.5px] leading-relaxed space-y-2.5 overflow-y-auto tech-scrollbar pr-1 text-[#D4D4D4]/90">
            {copilotMessages.map(msg => (
              <div key={msg.id} className="border-l-2 border-signal-green/30 pl-2">
                <span className="text-signal-green font-bold">&gt; </span>
                {msg.text}
              </div>
            ))}
          </div>
        </div>

        {/* System Logs */}
        <div className="bg-[#1A1A1A] border border-white/10 p-4 rounded-sm flex flex-col h-56 shadow-md">
          <div className="text-[11px] font-bold text-white uppercase tracking-wider border-b border-white/10 pb-2 mb-3">
            LOGS TIMELINE
          </div>

          <div className="flex-1 text-[11px] space-y-2 overflow-y-auto tech-scrollbar pr-1">
            {logs.slice().reverse().map(log => (
              <div key={log.id} className={`flex items-start gap-2 ${
                log.type === 'critical' ? 'text-threat-red bg-threat-red/5 p-1 border-l-2 border-threat-red' : 'text-[#D4D4D4]/70'
              }`}>
                <span className="text-data-blue shrink-0">{log.timestamp}</span>
                <span className="truncate">{log.message}</span>
              </div>
            ))}
          </div>
        </div>

        {/* RF Waterfall Canvas (Visual Simulation) */}
        <div className="bg-[#1A1A1A] border border-white/10 p-4 rounded-sm flex flex-col h-56 shadow-md">
          <div className="text-[11px] font-bold text-white uppercase tracking-wider border-b border-white/10 pb-2 mb-3">
            RF WATERFALL WAVEFORM
          </div>

          <div className="flex-1 relative bg-[#0D0D0D] border border-white/10 overflow-hidden flex flex-col items-center justify-center p-[2px] rounded-sm">
            {/* Real sliding mesh animation */}
            <div className="absolute inset-0 bg-gradient-to-b from-signal-green/15 via-void/40 to-void bg-[length:100%_20px] waterfall-slide"></div>
            
            {/* Visual waterfall line bars */}
            <div className="relative w-full h-full flex items-end opacity-40">
              <div className="w-1/12 bg-[#4fc3f7] h-[20%] animate-pulse m-[1px]"></div>
              <div className="w-1/12 bg-signal-green h-[50%] animate-pulse m-[1px]"></div>
              <div className="w-1/12 bg-signal-green h-[80%] animate-pulse m-[1px]"></div>
              <div className="w-1/12 bg-signal-green h-[40%] animate-pulse m-[1px]"></div>
              <div className="w-1/12 bg-[#b794f4] h-[60%] animate-pulse m-[1px]"></div>
              <div className="w-1/12 bg-[#4fc3f7] h-[30%] animate-pulse m-[1px]"></div>
              <div className="w-1/12 bg-signal-green h-[75%] animate-pulse m-[1px]"></div>
              <div className="w-1/12 bg-threat-red h-[90%] animate-pulse m-[1px]"></div>
              <div className="w-1/12 bg-signal-green h-[45%] animate-pulse m-[1px]"></div>
              <div className="w-1/12 bg-signal-green h-[60%] animate-pulse m-[1px]"></div>
            </div>

            <div className="absolute top-2 left-2 text-[8px] bg-[#0D0D0D]/90 px-1 border border-white/10 text-signal-green uppercase tracking-widest font-bold">
              10.82 GHz STAMP
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
