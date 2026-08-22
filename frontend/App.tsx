import { useState, useEffect } from 'react';
import { ScreenType, TelemetryState, SystemLog, CopilotMessage, SatelliteState } from './types';
import { useDeadsat } from './useDeadsat';
import LandingPage from './components/LandingPage';
import AuthScreen from './components/AuthScreen';
import TelemetryConsole from './components/TelemetryConsole';
import AiDiagnostics from './components/AiDiagnostics';
import SecurityConsole from './components/SecurityConsole';
import OperatorPanel from './components/OperatorPanel';
import SatelliteDashboard from './components/SatelliteDashboard';
import OperatorControlPanel from './components/OperatorControlPanel';
import { Shield, Radio, Activity, Cpu, Settings, Bell, X, Info } from './components/Icons';

export default function App() {
  const [currentRoute, setCurrentRoute] = useState<'landing' | 'auth' | 'dashboard'>('landing');
  const [activeTab, setActiveTab] = useState<ScreenType>('satellite-dashboard');
  
  // Operator states
  const [operatorId, setOperatorId] = useState<string | null>(null);
  const [activeProtocol, setActiveProtocol] = useState<'dilithium' | 'rsa' | null>(null);

  // Satellite Configurations
  const [satState, setSatState] = useState<SatelliteState>({
    name: 'CARTOSAT-3',
    noradId: '44804',
    orbitClass: 'LEO',
    decayTimeSeconds: 2539,
    signalLock: false,
    activeKeyType: 'NONE',
    anomalyDetectionEnabled: true,
    automatedRecoveryActive: false,
  });

  // Time & GMT countdown clocks
  const [gmtClock, setGmtClock] = useState('');
  useEffect(() => {
    const clockTimer = setInterval(() => {
      const now = new Date();
      setGmtClock(now.toISOString().substring(11, 19));
    }, 1000);
    return () => clearInterval(clockTimer);
  }, []);

  // ── LIVE BACKEND CONNECTION ──────────────────────────────────────
  // Everything below used to be driven by Math.random() on a timer. It now
  // comes from Pi #1 over /ws/telemetry and /ws/events. `simulated` is true
  // only while the backend is unreachable, and the header says so rather
  // than presenting invented numbers as live telemetry.
  const {
    telemetry,
    frame,
    logs,
    links,
    connected,
    simulated,
    ping,
    pushLog,
  } = useDeadsat();

  // AI Copilot Messages state
  const [copilotMessages, setCopilotMessages] = useState<CopilotMessage[]>([
    { id: '1', timestamp: '14:00:15', text: 'Analyzing attitude telemetry drift...', type: 'info' },
    { id: '2', timestamp: '14:01:30', text: 'Detected variance in attitude pitch parameters.', type: 'warning' },
    { id: '3', timestamp: '14:02:45', text: 'Warning: Attitude stabilization coils drawing high current.', type: 'alert' }
  ]);

  // Reflect the live satellite identity from the backend telemetry frame.
  useEffect(() => {
    if (!frame?.norad_id) return;
    setSatState(prev =>
      prev.noradId === String(frame.norad_id)
        ? prev
        : { ...prev, noradId: String(frame.norad_id) }
    );
  }, [frame?.norad_id]);

  // Surface real fault state from the emulator in the copilot feed.
  useEffect(() => {
    if (!frame?.fault_injected || frame.fault_injected === 'none') return;
    setCopilotMessages(prev => {
      const text = `Emulator reports active fault: ${frame.fault_injected}. Health: ${frame.overall_health ?? 'unknown'}.`;
      if (prev[prev.length - 1]?.text === text) return prev;
      return [...prev.slice(-19), {
        id: `${Date.now()}`,
        timestamp: new Date().toLocaleTimeString(),
        text,
        type: 'alert'
      }];
    });
  }, [frame?.fault_injected, frame?.overall_health]);

  // Real ping — measures actual round-trip to Pi #1 instead of printing "42ms".
  const handlePing = () => { void ping(); };

  // Auth gate check success
  const handleAuthSuccess = (opId: string, protocol: 'dilithium' | 'rsa') => {
    setOperatorId(opId);
    setActiveProtocol(protocol);
    setSatState(prev => ({
      ...prev,
      signalLock: true,
      activeKeyType: protocol === 'dilithium' ? 'DILITHIUM' : 'RSA_VULNERABLE'
    }));

    // Append authorized console log (logs now live in the useDeadsat hook)
    const timeStr = new Date().toLocaleTimeString();
    pushLog({
      id: Date.now().toString(),
      timestamp: timeStr,
      message: `ACCESS GRANTED. Operator ${opId} utilizing ${protocol.toUpperCase()}`,
      type: 'nominal',
      category: 'security',
    });

    setCopilotMessages(prev => [
      ...prev,
      { id: Date.now().toString(), timestamp: timeStr, text: `Uplink secured. Operator session initialized for terminal commands controls.`, type: 'info' }
    ]);

    setCurrentRoute('dashboard');
  };

  return (
    <div className="min-h-screen bg-[#0D0D0D] select-none overflow-x-hidden">
      
      {/* Route Switcher */}
      {currentRoute === 'landing' && (
        <LandingPage 
          satState={satState}
          onStartRecovery={() => setCurrentRoute('auth')}
        />
      )}

      {currentRoute === 'auth' && (
        <AuthScreen 
          onAuthSuccess={handleAuthSuccess}
          onCancel={() => setCurrentRoute('landing')}
        />
      )}

      {currentRoute === 'dashboard' && (
        <div className="min-h-screen flex flex-col pt-16">
          
          {/* TOP MISSION HEADER BAR */}
          <header className="fixed top-0 left-0 w-full h-16 bg-[#0D0D0D]/95 backdrop-blur-md border-b border-white/10 flex items-center justify-between px-6 z-50 shadow-md">
            <div className="flex items-center gap-4">
              <span className="font-display text-base font-black text-signal-green tracking-tighter uppercase">
                DEADSAT-RESURRECTION
              </span>
              
              {/* Heartbeat Uptime status lines */}
              <div className="hidden md:flex items-center gap-2 border-l border-white/10 pl-4 h-full">
                <span className="font-mono text-[10px] text-[#D4D4D4]/60 font-bold">SYS_HEALTH:</span>
                <svg viewBox="0 0 100 20" height="20" width="60" className="stroke-signal-green fill-none stroke-2">
                  <path className="heartbeat-path" d="M0,10 L20,10 L30,2 L40,18 L50,10 L100,10" />
                </svg>
              </div>
            </div>

            {/* Quick clock feeds */}
            <div className="flex items-center gap-6 font-mono text-xs">
              {/* LIVE LINK STATUS — real WebSocket state, not decoration */}
              <div
                title={
                  links
                    ? Object.entries(links.links)
                        .map(([k, v]) => `${k}: ${v.connected ? 'OK' : 'DOWN'} — ${v.detail}`)
                        .join('\n')
                    : 'Link status unavailable'
                }
                className={`hidden sm:flex items-center gap-1.5 px-2.5 py-1 border rounded-sm text-[10px] uppercase font-bold tracking-wider cursor-help ${
                  connected
                    ? 'border-signal-green/35 bg-signal-green/10 text-signal-green'
                    : 'border-threat-red/35 bg-threat-red/10 text-threat-red'
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    connected ? 'bg-signal-green animate-pulse' : 'bg-threat-red'
                  }`}
                ></span>
                <span>{connected ? 'LIVE TM' : 'SIMULATED'}</span>
                {links && (
                  <span className="opacity-60 ml-1">
                    {Object.values(links.links).filter((l) => l.connected).length}/
                    {Object.keys(links.links).length}
                  </span>
                )}
              </div>

              <div className="hidden sm:block text-signal-green tracking-widest animate-pulse font-bold">
                GMT: {gmtClock || '14:02:45'} | T-00:15:30
              </div>

              {/* Logged in operators credentials profile */}
              {operatorId && (
                <div className="bg-signal-green/10 text-signal-green px-3 py-1 pb-1.5 border border-signal-green/35 text-[10px] uppercase font-bold tracking-wider flex items-center gap-1.5 rounded-sm">
                  <span className="w-1.5 h-1.5 bg-signal-green rounded-full"></span>
                  <span>{operatorId}</span>
                </div>
              )}

              <button 
                onClick={() => setCurrentRoute('landing')}
                className="bg-[#1A1A1A] border border-white/15 text-[#D4D4D4] text-[10px] uppercase font-bold tracking-wider px-3.5 py-1.5 rounded-sm hover:border-threat-red hover:text-threat-red transition-all cursor-pointer font-sans"
              >
                ABORT / REBOOT
              </button>
            </div>
          </header>

          {/* SIDEBAR NAVIGATION + DENSE DASHBOARD CONTAINER SCREEN */}
          <div className="flex-1 flex flex-row relative">
            
            {/* LEFT COMPACT SIDEBAR NAVIGATION PANEL */}
            <aside className="fixed left-0 top-16 bottom-0 w-[80px] md:w-[260px] bg-[#131313]/95 border-r border-white/10 flex flex-col py-6 px-3 z-40 transition-all duration-300">
              
              <div className="px-3 mb-8 whitespace-nowrap overflow-hidden">
                <div className="font-display font-black text-sm text-signal-green uppercase tracking-wide truncate">
                  RECOVERY-01
                </div>
                <div className="font-mono text-[9px] text-[#4fc3f7] mt-0.5 tracking-wider uppercase font-bold">
                  STATUS: {telemetry.adcsStability === 'NOMINAL' ? 'NOMINAL' : 'HARDWARE_ALERT'}
                </div>
              </div>

              {/* Tab options lists */}
              <nav className="flex-1 space-y-2">
                <button 
                  onClick={() => setActiveTab('satellite-dashboard')}
                  className={`w-full flex items-center gap-3 px-3 py-3 rounded-sm transition-all border-l-2 ${
                    activeTab === 'satellite-dashboard'
                      ? 'border-signal-green bg-signal-green/10 text-signal-green font-black tracking-wider'
                      : 'border-transparent text-[#D4D4D4] hover:text-white'
                  }`}
                >
                  <Activity className="w-5 h-5 text-signal-green" />
                  <span className="hidden md:block font-mono text-[11px] font-bold uppercase tracking-wider font-display">Satellite Dashboard</span>
                </button>



                <button 
                  onClick={() => setActiveTab('telemetry')}
                  className={`w-full flex items-center gap-3 px-3 py-3 rounded-sm transition-all border-l-2 ${
                    activeTab === 'telemetry'
                      ? 'border-signal-green bg-signal-green/10 text-signal-green font-black tracking-wider'
                      : 'border-transparent text-[#D4D4D4] hover:text-white'
                  }`}
                >
                  <Radio className="w-5 h-5" />
                  <span className="hidden md:block font-mono text-[11px] font-bold uppercase tracking-wider font-display">Telemetry Live</span>
                </button>

                <button 
                  onClick={() => setActiveTab('diagnostics')}
                  className={`w-full flex items-center gap-3 px-3 py-3 rounded-sm transition-all border-l-2 ${
                    activeTab === 'diagnostics'
                      ? 'border-signal-green bg-signal-green/10 text-signal-green font-black tracking-wider'
                      : 'border-transparent text-[#D4D4D4] hover:text-white'
                  }`}
                >
                  <Cpu className="w-5 h-5" />
                  <span className="hidden md:block font-mono text-[11px] font-bold uppercase tracking-wider font-display">AI Diagnostics</span>
                </button>

                <button 
                  onClick={() => setActiveTab('security')}
                  className={`w-full flex items-center gap-3 px-3 py-3 rounded-sm transition-all border-l-2 ${
                    activeTab === 'security'
                      ? 'border-signal-green bg-signal-green/10 text-signal-green font-black tracking-wider'
                      : 'border-transparent text-[#D4D4D4] hover:text-white'
                  }`}
                >
                  <Shield className="w-5 h-5" />
                  <span className="hidden md:block font-mono text-[11px] font-bold uppercase tracking-wider font-display">PQC Security</span>
                </button>

                <button 
                  onClick={() => setActiveTab('operator')}
                  className={`w-full flex items-center gap-3 px-3 py-3 rounded-sm transition-all border-l-2 ${
                    activeTab === 'operator'
                      ? 'border-signal-green bg-signal-green/10 text-signal-green font-black tracking-wider'
                      : 'border-transparent text-[#D4D4D4] hover:text-white'
                  }`}
                >
                  <Settings className="w-5 h-5" />
                  <span className="hidden md:block font-mono text-[11px] font-bold uppercase tracking-wider font-display">Cmd Operator</span>
                </button>


              </nav>

              {/* Small branding footer tags */}
              <div className="mt-auto px-3 border-t border-white/10 pt-4 hidden md:block">
                {/* Must match GROUND_STATION in emulator/contact_calculator.py —
                    every AOS/LOS window on this dashboard is computed from those
                    coordinates. This read "NEW DELHI_HQ / 28.61 / 77.20", which
                    is not where the contact calculator thinks the antenna is. */}
                <div className="font-mono text-[8px] text-[#D4D4D4]/40 uppercase leading-normal">
                  STN_LOC: AHMEDABAD_HQ
                </div>
                <div className="font-mono text-[8px] text-[#D4D4D4]/40 uppercase mt-0.5">
                  LAT: 23.02 / LNG: 72.57
                </div>
              </div>
            </aside>

            {/* MAIN INTERACTIVE GRAPH CANVAS CONTAINER ROUTING */}
            <main className="ml-[80px] md:ml-[260px] flex-1 p-6 lg:p-8 bg-[#0D0D0D] relative min-h-[calc(100vh-64px)] flex flex-col">
              
              {/* Scanline grid laser */}
              <div className="absolute inset-0 pointer-events-none overflow-hidden z-0 bg-grid opacity-10"></div>

              {/* Live Sub Tab Content matching select selection */}
              <div className="relative z-10 flex-1 flex flex-col">
                {activeTab === 'satellite-dashboard' && (
                  <div className="flex flex-col gap-6">
                    <SatelliteDashboard />
                    <div className="border-t border-white/10 pt-6">
                      <OperatorControlPanel />
                    </div>
                  </div>
                )}

                {activeTab === 'telemetry' && (
                  <TelemetryConsole 
                    telemetry={telemetry}
                    onPing={handlePing}
                    logs={logs}
                    copilotMessages={copilotMessages}
                  />
                )}

                {activeTab === 'diagnostics' && (
                  <AiDiagnostics />
                )}

                {activeTab === 'security' && (
                  <SecurityConsole />
                )}

                {activeTab === 'operator' && (
                  <OperatorPanel />
                )}


              </div>
            </main>

          </div>
        </div>
      )}

    </div>
  );
}
