import { useState, useEffect, useRef } from 'react';
import { SatelliteState } from '../types';
import { Shield, Radio, Activity, Cpu, ChevronRight, Check, Lock, Terminal, Info } from './Icons';
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

// Register standard ScrollTrigger plugin
gsap.registerPlugin(ScrollTrigger);

interface LandingPageProps {
  satState: SatelliteState;
  onStartRecovery: () => void;
}

export default function LandingPage({ satState, onStartRecovery }: LandingPageProps) {
  const [activeFeatureTab, setActiveFeatureTab] = useState<'diagnostics' | 'security' | 'orchestration'>('diagnostics');
  
  const containerRef = useRef<HTMLDivElement>(null);
  const mockupRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ctx = gsap.context(() => {
      // Hero elements fade in and slide up stagger
      gsap.from(".gsap-hero-animate", {
        opacity: 0,
        y: 40,
        stagger: 0.12,
        duration: 1,
        ease: "power2.out"
      });

      // Perspective scale & rotation scroll effect on mock dashboard
      if (mockupRef.current) {
        gsap.fromTo(mockupRef.current, 
          { 
            transform: "perspective(1200px) rotateX(12deg) scale(0.93)", 
            opacity: 0.5,
          },
          {
            transform: "perspective(1200px) rotateX(0deg) scale(1)",
            opacity: 1,
            duration: 1.5,
            ease: "power1.out",
            scrollTrigger: {
              trigger: mockupRef.current,
              start: "top 90%",
              end: "top 45%",
              scrub: 1,
            }
          }
        );
      }

      // Feature cards stagger fade and rise on scroll
      const cards = gsap.utils.toArray(".gsap-card-animate");
      if (cards.length > 0) {
        gsap.fromTo(cards,
          { opacity: 0, y: 45 },
          {
            opacity: 1,
            y: 0,
            stagger: 0.15,
            duration: 0.8,
            ease: "power2.out",
            scrollTrigger: {
              trigger: "#platform",
              start: "top 80%",
              toggleActions: "play none none none"
            }
          }
        );
      }

      // Trust/Social proof metrics fade in
      gsap.from(".gsap-metrics-animate", {
        opacity: 0,
        y: 20,
        stagger: 0.1,
        duration: 0.8,
        ease: "power2.out",
        scrollTrigger: {
          trigger: ".gsap-metrics-trigger",
          start: "top 90%"
        }
      });
    }, containerRef);

    return () => ctx.revert();
  }, []);

  return (
    <div ref={containerRef} className="min-h-screen text-[#F5F5F5] bg-[#0A0A0C] font-sans antialiased relative overflow-x-hidden selection:bg-signal-green selection:text-black">
      
      {/* Subtle Background Layer: Extremely low opacity grid + premium radial gradients */}
      <div className="absolute inset-0 bg-grid opacity-[0.06] pointer-events-none z-0"></div>
      
      {/* Top soft glowing ambient orb */}
      <div className="absolute top-[-20%] left-1/2 -translate-x-1/2 w-[800px] h-[500px] bg-gradient-to-b from-[#1BF4AA]/10 via-transparent to-transparent rounded-full blur-[120px] pointer-events-none z-0"></div>
      
      {/* Header Bar */}
      <header className="relative z-20 max-w-7xl mx-auto px-6 h-20 flex items-center justify-between border-b border-white/[0.05]">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-signal-green to-[#00E5FF] flex items-center justify-center shadow-lg shadow-signal-green/10">
            <Shield className="w-4 h-4 text-black" />
          </div>
          <span className="font-display font-black text-white text-lg tracking-tight uppercase">
            DEADSAT-RESURRECTION
          </span>
        </div>

        <nav className="hidden md:flex items-center gap-8 text-xs font-mono tracking-wider text-[#D4D4D4]/70 uppercase">
          <a href="#solutions" className="hover:text-white transition-colors">Solutions</a>
          <a href="#platform" className="hover:text-white transition-colors">Platform Specs</a>
          <a href="#security" className="hover:text-white transition-colors">Crypto Trust</a>
          <a href="#verification" className="hover:text-white transition-colors">Compliance</a>
        </nav>

        <button 
          onClick={onStartRecovery}
          className="px-4 py-2 text-xs font-mono border border-white/[0.15] text-[#D4D4D4] rounded-md hover:border-signal-green hover:text-signal-green transition-all cursor-pointer font-bold uppercase tracking-wider"
        >
          Secure Access
        </button>
      </header>

      {/* Main Container */}
      <main className="relative z-10">

        {/* HERO SECTION */}
        <section className="max-w-6xl mx-auto px-6 pt-20 pb-24 text-center flex flex-col items-center">
          
          {/* Subtle Feature Badge */}
          <div className="inline-flex items-center gap-1.5 bg-white/[0.03] border border-white/[0.08] px-3 py-1 rounded-full text-[10.5px] text-white/80 font-mono uppercase tracking-wider mb-8 shadow-sm gsap-hero-animate">
            <span className="w-1.5 h-1.5 bg-signal-green rounded-full animate-pulse"></span>
            <span>Active Enterprise Security Platform</span>
          </div>

          {/* Headline */}
          <h1 className="font-display text-4xl sm:text-6xl lg:text-7xl font-extrabold tracking-tight leading-[1.05] text-white max-w-4xl mb-6 gsap-hero-animate">
            Unified Post-Quantum Security for <span className="bg-clip-text text-transparent bg-gradient-to-r from-white via-white to-signal-green">Critical Operations</span>
          </h1>

          {/* Subheading */}
          <p className="text-base sm:text-xl text-[#A1A1AA] max-w-3xl leading-relaxed mb-10 gsap-hero-animate">
            DEADSAT-RESURRECTION shields high-stakes telemetry, deep-edge telemetry link grids, and aerospace infrastructure. Prevent downtime with real-time neural anomaly diagnostics and CRYSTALS-Dilithium cryptographic resilience.
          </p>

          {/* Primary Call to Action */}
          <div className="flex flex-col sm:flex-row gap-4 justify-center mb-16 select-none gsap-hero-animate">
            <button 
              onClick={onStartRecovery}
              className="px-8 py-4 bg-signal-green hover:bg-[#D4FF00] text-black font-semibold rounded-md shadow-[0_4px_24px_rgba(27,244,170,0.15)] hover:shadow-[0_4px_32px_rgba(204,255,0,0.25)] transition-all cursor-pointer text-xs font-mono tracking-widest uppercase flex items-center justify-center gap-2"
            >
              <Lock className="w-4 h-4 text-black" />
              <span>LAUNCH SECURE CONSOLE</span>
            </button>
          </div>

          {/* Social Proof / Trust Indicators */}
          <div className="border-t border-b border-white/[0.05] py-6 w-full max-w-4xl grid grid-cols-1 sm:grid-cols-3 gap-6 text-center text-[#A1A1AA] gsap-metrics-trigger">
            <div className="flex flex-col items-center justify-center gsap-metrics-animate">
              <span className="text-[10px] uppercase tracking-widest font-mono text-white/40 block mb-1">PQC CRYPTO ALGORITHM</span>
              <span className="text-sm font-semibold text-[#D4D4D4]">NIST Crystals-Dilithium3</span>
            </div>
            <div className="flex flex-col items-center justify-center border-t sm:border-t-0 sm:border-l sm:border-r border-white/[0.05] py-4 sm:py-0 gsap-metrics-animate">
              <span className="text-[10px] uppercase tracking-widest font-mono text-white/40 block mb-1">DIAGNOSTIC LATENCY</span>
              <span className="text-sm font-semibold text-[#D4D4D4]">&lt;2.4ms Neural Evaluation</span>
            </div>
            <div className="flex flex-col items-center justify-center gsap-metrics-animate">
              <span className="text-[10px] uppercase tracking-widest font-mono text-white/40 block mb-1">UPLINK AUTHENTICATION</span>
              <span className="text-sm font-semibold text-[#D4D4D4]">Zero-Trust Dual Custody</span>
            </div>
          </div>

        </section>

        {/* INTERACTIVE COMPONENT PREVIEW AREA */}
        <section id="solutions" className="max-w-5xl mx-auto px-6 pb-24 relative">
          
          {/* Subtle Ambient glow behind the card */}
          <div className="absolute inset-x-12 top-10 bottom-10 bg-gradient-to-r from-signal-green/5 to-[#00D5FF]/5 rounded-[32px] blur-[80px] pointer-events-none z-0"></div>

          {/* Clean Modern SaaS Card Mockup */}
          <div ref={mockupRef} className="relative z-10 bg-[#121215] border border-white/[0.08] rounded-xl shadow-2xl shadow-black/80 overflow-hidden">
            
            {/* Chrome Bar */}
            <div className="bg-[#18181C] border-b border-white/[0.06] px-5 py-3.5 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-[#FF5F56]"></div>
                <div className="w-3 h-3 rounded-full bg-[#FFBD2E]"></div>
                <div className="w-3 h-3 rounded-full bg-[#27C93F]"></div>
                <span className="text-xs font-mono text-[#D4D4D4]/40 ml-4 font-normal">https://platform.deadsat-resurrection.io/dashboard</span>
              </div>
              <div className="flex gap-2">
                <span className="text-[10px] font-mono text-signal-green bg-signal-green/10 border border-signal-green/20 px-2.5 py-0.5 rounded-sm">
                  SECURED LINK
                </span>
              </div>
            </div>

            {/* Simulated UI layout inside landing page */}
            <div className="grid grid-cols-1 md:grid-cols-4 min-h-[380px]">
              
              {/* Left sidebar nav mockup */}
              <div className="bg-[#141418] border-r border-white/[0.05] p-5 space-y-4">
                <div className="font-mono text-[9px] font-bold text-white/30 uppercase tracking-widest">Active Channels</div>
                <div className="space-y-1.5 font-mono text-[11px]">
                  <div className="flex items-center gap-2 text-white bg-white/[0.03] p-2 rounded border-l-2 border-signal-green">
                    <Activity className="w-3.5 h-3.5 text-signal-green" />
                    <span>Diagnostics Feed</span>
                  </div>
                  <div className="flex items-center gap-2 text-[#D4D4D4]/60 p-2 rounded hover:text-white transition-all cursor-pointer">
                    <Shield className="w-3.5 h-3.5" />
                    <span>Lattice Keys</span>
                  </div>
                  <div className="flex items-center gap-2 text-[#D4D4D4]/60 p-2 rounded hover:text-white transition-all cursor-pointer">
                    <Cpu className="w-3.5 h-3.5" />
                    <span>Recovery Logs</span>
                  </div>
                </div>

                <div className="pt-8 space-y-2">
                  <div className="bg-[#1C1C22] border border-white/[0.05] p-3 rounded-md text-center text-[10px] font-mono">
                    <div className="text-[#D4D4D4]/40 uppercase tracking-widest text-[8px] mb-1">Target Endpoint</div>
                    <div className="text-white font-bold">{satState.name}</div>
                    <div className="text-[#00D5FF] mt-1 text-[9px]">Class LEO Bus</div>
                  </div>
                </div>
              </div>

              {/* Main Feed panel mockup */}
              <div className="md:col-span-3 p-6 flex flex-col justify-between">
                <div>
                  <div className="flex justify-between items-start border-b border-white/[0.05] pb-4 mb-4">
                    <div>
                      <h4 className="font-sans font-bold text-base text-white">Dynamic Asset Threat Overview</h4>
                      <p className="text-[11px] text-[#A1A1AA] mt-1 font-sans">Enterprise link state analytics updated real-time over secure gRPC gateway.</p>
                    </div>
                    <span className="text-xs font-mono bg-[#1C1C22] p-1.5 rounded text-[#D4D4D4]/80">
                      STATION: IND_HQ
                    </span>
                  </div>

                  {/* Operational stats list */}
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4 select-none">
                    <div className="bg-[#18181D] border border-white/[0.05] p-4 rounded-lg">
                      <div className="text-[10px] text-[#A1A1AA] uppercase tracking-wider font-mono">Active Link Status</div>
                      <div className="text-signal-green font-bold text-lg mt-1 font-mono">ENCRYPTED</div>
                    </div>
                    <div className="bg-[#18181D] border border-white/[0.05] p-4 rounded-lg">
                      <div className="text-[10px] text-[#A1A1AA] uppercase tracking-wider font-mono">Anomaly Classifier</div>
                      <div className="text-white font-bold text-lg mt-1 font-mono">NOMINAL</div>
                    </div>
                    <div className="bg-[#18181D] border border-white/[0.05] p-4 rounded-lg">
                      <div className="text-[10px] text-[#A1A1AA] uppercase tracking-wider font-mono">Quantum Risk Index</div>
                      <div className="text-[#00E5FF] font-bold text-lg mt-1 font-mono">ZERO SECURE</div>
                    </div>
                  </div>

                  {/* Clean code block of Lattice verification metrics */}
                  <div className="bg-[#0C0C0E] border border-white/[0.04] rounded-lg p-3.5 font-mono text-[10px] text-[#A1A1AA] space-y-1">
                    <div className="flex items-center gap-1.5 text-white/50 border-b border-white/[0.03] pb-1.5 mb-1.5 font-bold">
                      <Terminal className="w-3.5 h-3.5 text-[#00E5FF]" />
                      <span>CRYPTO_LEDGER_STANDBY</span>
                    </div>
                    <div>&gt; verifying peer signature with crystals-dilithium... OK</div>
                    <div>&gt; asymmetric multi-dimensional polynomial bounds matched successfully</div>
                    <div>&gt; telemetry verification key hash envelope: 0x9A4BEFB79AD... [committed]</div>
                  </div>
                </div>

                <div className="mt-6 flex flex-col sm:flex-row justify-between items-center bg-[#18181D] border border-white/[0.05] p-3 rounded-lg gap-3">
                  <div className="flex items-center gap-2">
                    <Info className="w-4 h-4 text-signal-green" />
                    <span className="text-[10.5px] text-[#D4D4D4]/80">System state synched with Indian telemetry stations.</span>
                  </div>
                  <button 
                    onClick={onStartRecovery} 
                    className="bg-signal-green hover:bg-[#D4FF00] text-black text-[10px] font-mono font-bold uppercase tracking-wider px-3.5 py-1.5 rounded-sm cursor-pointer transition-all border border-transparent whitespace-nowrap"
                  >
                    Launch Console
                  </button>
                </div>
              </div>

            </div>

          </div>
        </section>

        {/* SECTION 3: Key Enterprise Platform Capabilities */}
        <section id="platform" className="py-24 bg-[#0F0F12] border-t border-b border-white/[0.05] relative overflow-hidden">
          <div className="max-w-6xl mx-auto px-6 relative z-10 font-sans">
            
            <div className="text-center mb-16">
              <span className="font-mono text-[10.1px] text-signal-green bg-signal-green/10 px-3 py-1.5 rounded-full border border-signal-green/35 text-xs font-bold uppercase tracking-widest">
                02 / SECURE SPECIFICATIONS
              </span>
              <h2 className="font-display text-4xl sm:text-5xl font-black text-white mt-4 uppercase tracking-tighter">
                PLATFORM CAPABILITIES
              </h2>
              <p className="text-[#A1A1AA] max-w-xl mx-auto mt-4 text-sm sm:text-base">
                DEADSAT-RESURRECTION protects valuable critical assets using high-reliability features designed for zero-trust environments.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              
              {/* Feature 1 */}
              <div className="bg-[#141418] border border-white/[0.06] p-8 hover:border-signal-green/30 transition-all group rounded-lg shadow-md gsap-card-animate">
                <div className="w-12 h-12 bg-white/[0.03] border border-white/[0.08] flex items-center justify-center rounded-lg mb-6 group-hover:scale-105 transition-transform">
                  <Cpu className="w-6 h-6 text-signal-green" />
                </div>
                <h3 className="font-sans font-bold text-lg text-white mb-3">AI Neural Diagnostics</h3>
                <p className="font-sans text-sm text-[#A1A1AA] leading-relaxed mb-4">
                  Deploy deep multi-channel sequence models to evaluate multi-spectral operations data. Pre-emptively flag variances in real-time with continuous neural confidence scoring.
                </p>
                <div className="text-[10px] font-mono text-signal-green bg-signal-green/5 border border-signal-green/10 px-2.5 py-1 rounded inline-block">
                  99.4% Accuracy Matrix
                </div>
              </div>

              {/* Feature 2 */}
              <div className="bg-[#141418] border border-white/[0.06] p-8 hover:border-[#00D5FF]/30 transition-all group rounded-lg shadow-md gsap-card-animate">
                <div className="w-12 h-12 bg-white/[0.03] border border-white/[0.08] flex items-center justify-center rounded-lg mb-6 group-hover:scale-105 transition-transform">
                  <Shield className="w-6 h-6 text-[#00D5FF]" />
                </div>
                <h3 className="font-sans font-bold text-lg text-white mb-3">Post-Quantum Resilience</h3>
                <p className="font-sans text-sm text-[#A1A1AA] leading-relaxed mb-4">
                  Built on modern NIST-selected post-quantum cryptographic standards. Utilize CRYSTALS-Dilithium key encapsulation sequences to defend uplink control paths against quantum attacks.
                </p>
                <div className="text-[10px] font-mono text-[#00D5FF] bg-[#00D5FF]/5 border border-[#00D5FF]/10 px-2.5 py-1 rounded inline-block">
                  Crystals-Dilithium3 / RSA Dual Wrap
                </div>
              </div>

              {/* Feature 3 */}
              <div className="bg-[#141418] border border-white/[0.06] p-8 hover:border-white/30 transition-all group rounded-lg shadow-md gsap-card-animate">
                <div className="w-12 h-12 bg-white/[0.03] border border-white/[0.08] flex items-center justify-center rounded-lg mb-6 group-hover:scale-105 transition-transform">
                  <Radio className="w-6 h-6 text-white" />
                </div>
                <h3 className="font-sans font-bold text-lg text-white mb-3">Autonomous Command Flow</h3>
                <p className="font-sans text-sm text-[#A1A1AA] leading-relaxed mb-4">
                  Interactive ledger orchestrates corrective scripts step-by-step. Secure agent-trace transparency enables ground controllers to inspect and authorize command chains.
                </p>
                <div className="text-[10px] font-mono text-[#D4D4D4] bg-white/[0.05] border border-white/[0.08] px-2.5 py-1 rounded inline-block">
                  Human-In-The-Loop Authorisation
                </div>
              </div>

            </div>

            {/* Bottom Conversion Strip */}
            <div className="mt-16 bg-gradient-to-r from-[#141418] via-white/[0.02] to-[#141418] border border-white/[0.05] p-8 rounded-lg flex flex-col md:flex-row justify-between items-center gap-6">
              <div>
                <h4 className="font-sans font-bold text-white text-base">Ready to safeguard your digital remote assets?</h4>
                <p className="text-xs text-[#A1A1AA] mt-1 font-sans">Verify cryptographic credentials through the secure quantum-safe identity verification gateway.</p>
              </div>
              <button
                onClick={onStartRecovery}
                className="px-6 py-3 bg-white text-black font-semibold text-xs font-mono uppercase rounded-md hover:bg-signal-green transition-all cursor-pointer whitespace-nowrap"
              >
                Access Secure Gateway
              </button>
            </div>

          </div>
        </section>

      </main>

      {/* FOOTER */}
      <footer id="verification" className="max-w-7xl mx-auto px-6 py-12 border-t border-white/[0.05] mt-12 grid grid-cols-1 sm:grid-cols-4 gap-8 font-mono text-xs text-[#A1A1AA]">
        <div>
          <div className="text-[10.5px] uppercase tracking-widest text-[#D4D4D4]/30 mb-3">01 / BRAND IDENTITY</div>
          <p className="text-[11px] leading-relaxed text-[#A1A1AA]/70">
            DEADSAT-RESURRECTION provides next-generation post-quantum cybersecurity capabilities and diagnostic telemetry monitoring platforms for remote edge networks and enterprise aerospace fleets.
          </p>
        </div>
        <div>
          <div className="text-[10.5px] uppercase tracking-widest text-[#D4D4D4]/30 mb-3">02 / CRYPTO MATRIX</div>
          <div className="space-y-1 text-[#D4D4D4]/80 text-[11px]">
            <div>• Lattice Dimension 1024</div>
            <div>• Private Key Length: 4016B</div>
            <div>• Core Collision Resistant</div>
          </div>
        </div>
        <div>
          <div className="text-[10.5px] uppercase tracking-widest text-[#D4D4D4]/30 mb-3">03 / COMPLIANCE RINGS</div>
          <div className="space-y-1 text-[#D4D4D4]/80 text-[11px]">
            <div>• NIST Round 3 PQC Draft</div>
            <div>• FIPS-203 Standardized Ready</div>
            <div>• ISO-9005 Critical Space</div>
          </div>
        </div>
        <div className="flex flex-col justify-between">
          <div>
            <div className="text-[10.5px] uppercase tracking-widest text-[#D4D4D4]/30 mb-3">04 / SECURE LEDGER STATUS</div>
            <div className="text-[11px] text-signal-green font-bold uppercase tracking-wider flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-signal-green rounded-full animate-ping"></span>
              <span>ALL CHANNELS SECURE</span>
            </div>
          </div>
          <p className="text-[10px] text-[#D4D4D4]/30 mt-4 leading-normal">
            DEADSAT-RESURRECTION Recovery v2.76_PRIMA<br/>
            UTC Reference: {new Date().toISOString().substring(0, 10).replace(/-/g, '/')}
          </p>
        </div>
      </footer>

    </div>
  );
}
