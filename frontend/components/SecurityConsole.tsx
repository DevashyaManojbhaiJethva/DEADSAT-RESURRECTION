import { useState } from 'react';
import { Shield, Lock, Warning, Check } from './Icons';

export default function SecurityConsole() {
  const [activeKey, setActiveKey] = useState<'DILITHIUM' | 'RSA_VULNERABLE'>('DILITHIUM');
  const [isRotating, setIsRotating] = useState(false);
  const [securityScore, setSecurityScore] = useState(99);
  const [activeLatticeMatrix, setActiveLatticeMatrix] = useState('LATTICE_MATRIX_MOD_q8380417');
  const [entropyBits, setEntropyBits] = useState(256);

  const handleKeyRotation = () => {
    setIsRotating(true);
    setTimeout(() => {
      setIsRotating(false);
      setActiveKey('DILITHIUM');
      setSecurityScore(100);
      setEntropyBits(384);
      setActiveLatticeMatrix('LATTICE_MATRIX_MOD_q10485761_ULTRA_SECURE');
    }, 2500);
  };

  return (
    <div className="flex-1 flex flex-col lg:flex-row gap-6 font-sans">
      
      {/* Prime Cryptographic controls */}
      <div className="flex-1 bg-[#1A1A1A]/95 border border-signal-green/20 p-6 rounded-sm flex flex-col justify-between shadow-lg relative overflow-hidden">
        <div>
          <div className="flex justify-between items-center border-b border-white/10 pb-3 mb-6">
            <h2 className="font-display text-xl font-black text-white uppercase tracking-tighter flex items-center gap-2">
              <Shield className="w-5 h-5 text-signal-green" />
              <span>POST-QUANTUM CRYPTO CONSOLE</span>
            </h2>
            <span className="font-mono text-[10px] bg-quantum-purple/10 text-quantum-purple font-bold px-2.5 py-0.5 rounded-sm uppercase tracking-wider">
              PQC STATUS: HARDENED
            </span>
          </div>

          <p className="text-sm text-[#D4D4D4] leading-relaxed mb-6">
            Civilian satellite lanes rely heavily on older RSA asymmetric signatures. Quantum Shor solvers can crack normal RSA sub-keys in linear times. Implementing CRYSTALS-Dilithium secures critical command uplink modules through multi-dimensional modular lattices.
          </p>

          {/* Crypkey Toggles */}
          <div className="grid grid-cols-2 gap-4 mb-6">
            <button 
              onClick={() => {
                setActiveKey('DILITHIUM');
                setSecurityScore(99);
              }}
              className={`p-4 border rounded-sm font-mono text-xs font-bold transition-all text-left ${
                activeKey === 'DILITHIUM'
                  ? 'border-quantum-purple bg-quantum-purple/5 text-quantum-purple glow-purple'
                  : 'border-white/10 text-[#D4D4D4] hover:border-white/20'
              }`}
            >
              <div className="uppercase">CRYSTALS-Dilithium3</div>
              <div className="text-[9px] text-[#D4D4D4]/60 mt-1 uppercase font-normal">Lattice key-encapsulation</div>
            </button>

            <button 
              onClick={() => {
                setActiveKey('RSA_VULNERABLE');
                setSecurityScore(12);
              }}
              className={`p-4 border rounded-sm font-mono text-xs font-bold transition-all text-left ${
                activeKey === 'RSA_VULNERABLE'
                  ? 'border-threat-red bg-threat-red/5 text-threat-red glow-error'
                  : 'border-white/10 text-[#D4D4D4] hover:border-white/20'
              }`}
            >
              <div className="uppercase">Legacy RSA-2048</div>
              <div className="text-[9px] text-threat-red mt-1 uppercase font-normal">SHOR_SOLVER VULNERABLE</div>
            </button>
          </div>

          {/* Security Stats */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
            <div className="bg-[#0D0D0D] p-4 rounded-sm border border-white/10">
              <div className="text-[10px] font-mono text-[#D4D4D4] uppercase font-bold tracking-wider">Quantum Security Index</div>
              <div className={`text-2xl font-black font-mono mt-1 ${activeKey === 'DILITHIUM' ? 'text-signal-green' : 'text-threat-red animate-pulse'}`}>
                {securityScore}%
              </div>
            </div>
            <div className="bg-[#0D0D0D] p-4 rounded-sm border border-white/10">
              <div className="text-[10px] font-mono text-[#D4D4D4] uppercase font-bold tracking-wider">Sign Key Entropy Speed</div>
              <div className="text-2xl font-black font-mono mt-1 text-white">
                {entropyBits} BITS
              </div>
            </div>
            <div className="bg-[#0D0D0D] p-4 rounded-sm border border-white/10">
              <div className="text-[10px] font-mono text-[#D4D4D4] uppercase font-bold tracking-wider">Lattice Dim Level</div>
              <div className="text-xs font-bold font-mono mt-2 text-data-blue truncate select-all">
                {activeLatticeMatrix}
              </div>
            </div>
          </div>
        </div>

        {/* Action triggers */}
        <div className="border-t border-white/10 pt-6 mt-6 flex flex-col sm:flex-row items-center justify-between gap-4 font-mono">
          <div className="text-[#D4D4D4] text-xs max-w-sm leading-relaxed">
            Dilithium signature key rotations alter the underlying lattice rings, locking out potential adversarial MITM hijackers.
          </div>

          <button 
            onClick={handleKeyRotation}
            disabled={isRotating}
            className="px-6 py-3.5 bg-quantum-purple text-black font-bold hover:bg-[#d8c2ff] hover:scale-[1.02] active:scale-[0.98] transition-all rounded-sm cursor-pointer border border-transparent glow-purple whitespace-nowrap uppercase tracking-widest text-xs"
          >
            {isRotating ? "COMPUTING LATTICE ENVELOPE..." : "ROTATE SECURE SIGNATURE KEY"}
          </button>
        </div>
      </div>

      {/* Crypkey block visualizer panel */}
      <div className="w-full lg:w-96 bg-[#1A1A1A] border border-white/10 p-5 rounded-sm flex flex-col h-fit shadow-md font-mono">
        <h3 className="text-xs font-bold text-white uppercase tracking-wider border-b border-white/10 pb-3 mb-4">
          LATTICE SIGNATURE DESTRUCT VECTOR
        </h3>

        {/* Visual Lattice grid */}
        <div className="grid grid-cols-6 gap-1 mb-6 relative">
          {Array.from({ length: 24 }).map((_, idx) => {
            const isRedSpot = (idx * 7) % 5 === 0;
            return (
              <div 
                key={idx} 
                className={`h-8 border flex items-center justify-center text-[8px] transition-all ${
                  isRotating ? 'animate-pulse bg-quantum-purple/35 border-quantum-purple' :
                  activeKey === 'RSA_VULNERABLE' && isRedSpot ? 'bg-threat-red/20 border-threat-red text-threat-red font-bold' :
                  'bg-[#0D0D0D]/80 border-white/10 text-signal-green/60'
                }`}
              >
                {activeKey === 'RSA_VULNERABLE' && isRedSpot ? "DECAY" : `0x${(idx * 14).toString(16).toUpperCase()}`}
              </div>
            );
          })}
          
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className={`p-2 bg-[#0D0D0D] border text-[10px] text-center font-bold font-mono tracking-wider shadow-lg rounded-sm ${
              activeKey === 'DILITHIUM' ? 'border-signal-green text-signal-green' : 'border-threat-red text-threat-red animate-pulse'
            }`}>
              {activeKey === 'DILITHIUM' ? "LATTICE_OK: CRYPT-HARDEN" : "CRITICAL_THREAT: WEAK_KEY"}
            </div>
          </div>
        </div>

        <div className="space-y-4">
          <div className="flex items-start gap-2 text-xs">
            {activeKey === 'DILITHIUM' ? (
              <Check className="w-5 h-5 text-signal-green mt-0.5 shrink-0" />
            ) : (
              <Warning className="w-5 h-5 text-threat-red mt-0.5 shrink-0 animate-pulse" />
            )}
            <p className="text-[#D4D4D4]/80 leading-relaxed">
              {activeKey === 'DILITHIUM' 
                ? "Secure socket keys are post-quantum hardened. Ground station signatures cannot be falsified by Shor solvers."
                : "Active RSA keys can be pre-cached by state-level attackers. Upgrade signature lanes to CRYSTALS immediately."}
            </p>
          </div>
        </div>
      </div>

    </div>
  );
}
