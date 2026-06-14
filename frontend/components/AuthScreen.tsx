import React, { useState, useEffect, useRef } from 'react';
import { Lock, Key, Terminal, Shield, Check } from './Icons';

interface AuthScreenProps {
  onAuthSuccess: (operatorId: string, protocol: 'dilithium' | 'rsa') => void;
  onCancel: () => void;
}

export default function AuthScreen({ onAuthSuccess, onCancel }: AuthScreenProps) {
  const [operatorId, setOperatorId] = useState('');
  const [accessKey, setAccessKey] = useState('');
  const [protocol, setProtocol] = useState<'dilithium' | 'rsa'>('dilithium');
  const [isSigning, setIsSigning] = useState(false);
  const [signingProgress, setSigningProgress] = useState(0);
  const [ceremonyLogs, setCeremonyLogs] = useState<string[]>([]);
  const logsContainerRef = useRef<HTMLDivElement>(null);

  const logsSequence = [
    "Establishing secure quantum-safe tunnel to ORU Ground Station...",
    "Injecting entropy seeds into post-quantum ring buffers...",
    "Operator ID verification: OK.",
    "Access Key handshake accepted.",
    "Loading selected protocol: CRYSTALS-Dilithium3 parameters...",
    "Generating public/private keypair (lattice dimensional depth = 280)...",
    "Signing recovery command header...",
    "Verifying post-quantum lattice verification ring signatures...",
    "Uplink channel handshake: VERIFIED. Code 200 SUCCESS.",
    "Uplink socket established. Granting full control panel access..."
  ];

  const rsaLogsSequence = [
    "Establishing standard SSH tunnel to Ground Station...",
    "Operator ID verification: OK.",
    "Access Key signature payload generated.",
    "WARNING: Selected legacy cryptography is vulnerable to Shor's algorithm.",
    "Bypassing PQC lattice signing layers (DANGEROUS)...",
    "Uplink channel handshake: SIGNATURE STABILIZED.",
    "Bypass audit logs updated.",
    "Uplink socket established. Granting fallback control..."
  ];

  // Auto-scroll logs as they populate
  useEffect(() => {
    if (logsContainerRef.current) {
      logsContainerRef.current.scrollTop = logsContainerRef.current.scrollHeight;
    }
  }, [ceremonyLogs]);

  const handleAuthenticate = (e: React.FormEvent) => {
    e.preventDefault();
    if (!operatorId || !accessKey) return;

    setIsSigning(true);
    setSigningProgress(0);
    setCeremonyLogs([]);

    const sequence = protocol === 'dilithium' ? logsSequence : rsaLogsSequence;
    let logIndex = 0;

    const interval = setInterval(() => {
      if (logIndex < sequence.length) {
        setCeremonyLogs(prev => [...prev, sequence[logIndex]]);
        setSigningProgress(Math.floor(((logIndex + 1) / sequence.length) * 100));
        logIndex++;
      } else {
        clearInterval(interval);
        setTimeout(() => {
          setIsSigning(false);
          onAuthSuccess(operatorId, protocol);
        }, 1000);
      }
    }, 600);
  };

  return (
    <div className="min-h-screen bg-[#0D0D0D] flex items-center justify-center p-6 bg-grid font-sans relative">
      {/* Scanline overlay */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden z-0">
        <div className="w-full h-[2px] bg-signal-green/10 scanline-effect shadow-[0_0_15px_rgba(204,255,0,0.3)]"></div>
        <div className="absolute inset-0 bg-grid-dense opacity-40"></div>
      </div>

      <div className="max-w-md w-full bg-[#1A1A1A]/95 border border-signal-green/20 p-8 rounded-sm relative z-10 shadow-[0_0_40px_rgba(0,0,0,0.85)]">
        {/* Decorative corner brackets */}
        <div className="absolute top-0 left-0 w-4 h-4 border-t-2 border-l-2 border-signal-green/40"></div>
        <div className="absolute top-0 right-0 w-4 h-4 border-t-2 border-r-2 border-signal-green/40"></div>
        <div className="absolute bottom-0 left-0 w-4 h-4 border-b-2 border-l-2 border-signal-green/40"></div>
        <div className="absolute bottom-0 right-0 w-4 h-4 border-b-2 border-r-2 border-signal-green/40"></div>

        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-full border border-signal-green/20 mb-4 bg-[#0D0D0D] relative overflow-hidden">
            <Lock className="w-6 h-6 text-signal-green animate-pulse" />
          </div>
          <h1 className="font-display text-3xl font-black text-white tracking-tighter uppercase leading-none">Authentication</h1>
          <p className="font-mono text-[10px] text-[#D4D4D4]/60 uppercase tracking-widest mt-1">DeadSat Resurrection Protocol</p>
        </div>

        <form onSubmit={handleAuthenticate} className="space-y-6">
          <div className="space-y-4">
            {/* Operator ID Input */}
            <div className="relative">
              <label className="font-mono text-[10px] uppercase font-bold text-[#D4D4D4]/60 absolute -top-2 left-3 bg-[#1A1A1A] px-1 z-10">Operator ID</label>
              <div className="relative">
                <input 
                  type="text" 
                  value={operatorId}
                  onChange={e => setOperatorId(e.target.value)}
                  placeholder="OP-XXXX" 
                  required
                  className="w-full bg-[#0D0D0D]/60 border border-white/10 text-signal-green font-mono font-bold p-3.5 pl-10 rounded-sm focus:outline-none focus:border-signal-green focus:ring-1 focus:ring-signal-green transition-all"
                />
                <Terminal className="w-4 h-4 text-[#D4D4D4]/60 absolute left-3.5 top-1/2 -translate-y-1/2" />
              </div>
            </div>

            {/* Access Key Input */}
            <div className="relative">
              <label className="font-mono text-[10px] uppercase font-bold text-[#D4D4D4]/60 absolute -top-2 left-3 bg-[#1A1A1A] px-1 z-10">Access Key</label>
              <div className="relative">
                <input 
                  type="password" 
                  value={accessKey}
                  onChange={e => setAccessKey(e.target.value)}
                  placeholder="••••••••••••••••" 
                  required
                  className="w-full bg-[#0D0D0D]/60 border border-white/10 text-signal-green font-mono p-3.5 pl-10 rounded-sm focus:outline-none focus:border-signal-green focus:ring-1 focus:ring-signal-green transition-all"
                />
                <Key className="w-4 h-4 text-[#D4D4D4]/60 absolute left-3.5 top-1/2 -translate-y-1/2" />
              </div>
            </div>
          </div>

          {/* Cryptographic Protocol Selector */}
          <div className="space-y-3">
            <span className="block font-mono text-[10px] uppercase font-bold text-[#D4D4D4]/60 tracking-wider border-b border-white/10 pb-1">Cryptographic Signatures</span>
            
            <label className="flex items-start gap-3 cursor-pointer group p-2 hover:bg-[#0D0D0D]/40 rounded transition-colors select-none">
              <input 
                type="radio" 
                name="crypto-protocol"
                checked={protocol === 'dilithium'}
                onChange={() => setProtocol('dilithium')}
                className="mt-1 accent-signal-green"
              />
              <div className="space-y-0.5">
                <span className="font-mono text-xs font-bold text-white group-hover:text-signal-green transition-colors">CRYSTALS-Dilithium3</span>
                <span className="block font-sans text-[10px] text-[#D4D4D4]/60 uppercase tracking-wide">Post-Quantum Safe Lattice Signatures</span>
              </div>
            </label>

            <label className="flex items-start gap-3 cursor-pointer group p-2 hover:bg-[#0D0D0D]/40 rounded transition-colors select-none">
              <input 
                type="radio" 
                name="crypto-protocol"
                checked={protocol === 'rsa'}
                onChange={() => setProtocol('rsa')}
                className="mt-1 accent-signal-green"
              />
              <div className="space-y-0.5 w-full">
                <span className="font-mono text-xs font-bold text-white group-hover:text-signal-green transition-colors">Legacy RSA-2048</span>
                <span className="block font-sans text-[10px] text-threat-red uppercase tracking-wide font-bold">Shor's Algorithm Vulnerable</span>
                
                {protocol === 'rsa' && (
                  <div className="mt-3 p-3 bg-threat-red/10 border border-threat-red/35 text-threat-red text-[10px] font-mono leading-relaxed rounded-sm">
                    WARNING: Quantum-computing processors (e.g., Shor solvers) can factor RSA keys. Use only for legacy emergency test lanes.
                  </div>
                )}
              </div>
            </label>
          </div>

          {/* Form Actions */}
          <div className="pt-2 flex flex-col gap-3 font-mono">
            <button 
              type="submit"
              className="w-full bg-signal-green text-black p-4 font-display text-xs font-black uppercase tracking-widest hover:bg-[#D4FF00] active:scale-[0.98] transition-all flex items-center justify-center gap-2 rounded-sm cursor-pointer border border-transparent glow-primary"
            >
              <Shield className="w-4 h-4 text-black" />
              <span>AUTHENTICATE</span>
            </button>
            <button 
              type="button"
              onClick={onCancel}
              className="w-full text-center text-[10px] text-[#D4D4D4]/60 hover:text-white py-1 cursor-pointer transition-colors uppercase font-bold tracking-widest"
            >
              CANCEL SECURITY ENGAGEMENT
            </button>
          </div>
        </form>

        {/* Small Uptime Status Labels */}
        <div className="mt-6 pt-4 border-t border-white/10 grid grid-cols-3 gap-2 font-mono text-[9px] text-[#D4D4D4]/40 text-center">
          <div className="flex flex-col items-center gap-0.5">
            <span className="w-1.5 h-1.5 rounded-full bg-signal-green glow-primary"></span>
            <span>STN_OK</span>
          </div>
          <div className="flex flex-col items-center gap-0.5">
            <span className="w-1.5 h-1.5 rounded-full bg-signal-green glow-primary animate-ping"></span>
            <span>AI_LIVE</span>
          </div>
          <div className="flex flex-col items-center gap-0.5">
            <span className="w-1.5 h-1.5 rounded-full bg-signal-green glow-primary"></span>
            <span>LYR_INIT</span>
          </div>
        </div>
      </div>

      {/* Signing Ceremony Modal Overlay */}
      {isSigning && (
        <div className="fixed inset-0 bg-[#0D0D0D]/90 backdrop-blur-md flex items-center justify-center z-50 p-6">
          <div className="max-w-md w-full bg-[#1A1A1A] border-2 border-signal-green p-8 rounded-sm relative z-50 font-mono shadow-2xl">
            <div className="text-center mb-6">
              <Shield className="w-12 h-12 text-signal-green mx-auto mb-3 animate-bounce" />
              <h2 className="text-white font-display text-xl font-bold tracking-tight uppercase">Initiating Signature</h2>
              <div className="text-data-blue text-xs tracking-widest uppercase mt-1">Lattice signing in progress</div>
            </div>

            {/* Simulated Live Key Signature Ceremony Scroll Log */}
            <div className="h-44 bg-[#0D0D0D] p-4 border border-white/10 text-xs space-y-2 overflow-y-auto mb-4 tech-scrollbar" ref={logsContainerRef}>
              {ceremonyLogs.map((log, idx) => (
                <div key={idx} className={idx === ceremonyLogs.length - 1 ? 'text-signal-green font-bold' : 'text-[#D4D4D4]/70'}>
                  &gt; {log}
                </div>
              ))}
            </div>

            {/* Beautiful Progress bar loader */}
            <div className="space-y-1">
              <div className="flex justify-between text-[10px] text-[#D4D4D4]/60">
                <span>KEY_SIGN_BURST</span>
                <span>{signingProgress}%</span>
              </div>
              <div className="w-full bg-[#0D0D0D] h-2 rounded-sm overflow-hidden border border-white/10">
                <div 
                  className="bg-signal-green h-full transition-all duration-300"
                  style={{ width: `${signingProgress}%` }}
                ></div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
