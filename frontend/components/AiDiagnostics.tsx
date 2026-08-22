import { useState, useEffect } from 'react';
import { Cpu, Warning, Check, Activity } from './Icons';
// WIRING: AI-1 status and classification now come from the backend.
import { api, subscribeTelemetry, TelemetryFrame } from '../api';

export default function AiDiagnostics() {
  const [anomalyThreshold, setAnomalyThreshold] = useState(85);
  const [activeModel, setActiveModel] = useState<'transformer_seq' | 'adcs_lstm'>('transformer_seq');
  // WIRING: real classifier confidence, not a random walk between 98.5–99.9.
  // 0 until AI-1 actually returns a classification.
  const [confidenceScore, setConfidenceScore] = useState(0);
  const [isCalibrating, setIsCalibrating] = useState(false);
  const [artifactsReady, setArtifactsReady] = useState<boolean | null>(null);
  const [statusHint, setStatusHint] = useState<string>('checking AI-1 …');
  const [lastClass, setLastClass] = useState<string>('—');

  // WIRING: the five rows are now live telemetry channels from the emulator
  // rather than fixed strings. Thresholds mirror the emulator's own limits.
  const [telemetryStreams, setTelemetryStreams] = useState([
    { name: 'ADCS rotational variance', value: '—', status: 'NOMINAL' },
    { name: 'OBC core temperature',     value: '—', status: 'NOMINAL' },
    { name: 'Solar array output',       value: '—', status: 'NOMINAL' },
    { name: 'OBC CPU load',             value: '—', status: 'NOMINAL' },
    { name: 'RF downlink strength',     value: '—', status: 'NOMINAL' },
  ]);

  // AI-1 artifact status
  useEffect(() => {
    let alive = true;
    api.pipelineStatus()
      .then((p: any) => {
        if (!alive) return;
        setArtifactsReady(Boolean(p.artifacts_ready));
        setStatusHint(
          p.artifacts_ready
            ? `transformer + isolation forest loaded · seq_len=${p.seq_len} · ${p.feature_cols?.length ?? 0} features`
            : (p.hint ?? 'artifacts missing — run train_classifier.py'),
        );
      })
      .catch((e: any) => {
        if (!alive) return;
        setArtifactsReady(false);
        setStatusHint(`AI-1 unreachable — ${e.message}`);
      });
    return () => { alive = false; };
  }, []);

  // Live telemetry -> the five diagnostic channels
  useEffect(() => {
    const sock = subscribeTelemetry((f: TelemetryFrame) => {
      const lvl = (bad: boolean, warn: boolean) => bad ? 'CRITICAL' : warn ? 'WARNING' : 'NOMINAL';
      setTelemetryStreams([
        { name: 'ADCS rotational variance',
          value: `${(f.adcs_rate_deg_s ?? 0).toFixed(3)} deg/s`,
          status: lvl(f.adcs_status === 'fault', (f.adcs_rate_deg_s ?? 0) > 0.05) },
        { name: 'OBC core temperature',
          value: `${(f.obc_temp_c ?? 0).toFixed(1)} °C`,
          status: lvl((f.obc_temp_c ?? 0) > 60, (f.obc_temp_c ?? 0) > 50) },
        { name: 'Solar array output',
          value: `${(f.power_w ?? 0).toFixed(1)} W`,
          status: lvl((f.power_w ?? 0) < 50, (f.power_w ?? 0) < 75) },
        { name: 'OBC CPU load',
          value: `${(f.obc_cpu_pct ?? 0).toFixed(1)} %`,
          status: lvl((f.obc_cpu_pct ?? 0) > 85, (f.obc_cpu_pct ?? 0) > 60) },
        { name: 'RF downlink strength',
          value: f.comms_downlink ? `${(f.signal_strength_dbm ?? 0).toFixed(1)} dBm` : 'DOWNLINK OFF',
          status: lvl(!f.comms_downlink, (f.signal_strength_dbm ?? -100) < -90) },
      ]);
    });
    return () => sock.close();
  }, []);

  // WIRING: "recalibrate" now runs a real AI-1 inference pass over the
  // current orbital window instead of hardcoding 99.82% after a 2 s timeout.
  const handleRecalibrate = async () => {
    setIsCalibrating(true);
    try {
      const r: any = await api.classify();
      setConfidenceScore(Number(((r.confidence ?? 0) * 100).toFixed(2)));
      setLastClass(`${r.fault_type} (raw: ${r.raw_fault_class}${r.anomaly_flag ? ', anomaly' : ''})`);
      setStatusHint(`classified from live window · NORAD ${r.norad_id}`);
    } catch (e: any) {
      setConfidenceScore(0);
      setLastClass('—');
      setStatusHint(`classification failed — ${e.message}`);
    } finally {
      setIsCalibrating(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col lg:flex-row gap-6 font-sans">
      
      {/* Simulation Controls Panel */}
      <div className="flex-1 bg-[#1A1A1A]/95 border border-signal-green/20 p-6 rounded-sm flex flex-col justify-between shadow-lg">
        <div>
          <div className="flex justify-between items-center border-b border-white/10 pb-3 mb-6">
            <h2 className="font-display text-xl font-black text-white uppercase tracking-tighter flex items-center gap-2">
              <Cpu className="w-5 h-5 text-signal-green" />
              <span>TRANSFORMER CLASSIFIER</span>
            </h2>
            <span className="font-mono text-[10px] bg-signal-green/10 text-signal-green font-bold px-2 py-0.5 rounded-sm uppercase tracking-wider">
              AUTO_MODELING: ACTIVE
            </span>
          </div>

          <p className="text-sm text-[#D4D4D4] leading-relaxed mb-6">
            The neural core evaluates high-dimensional telemetry streams in real-time. By comparing current attitude and power footprints against synthetic decay patterns, the model diagnoses faults within the bus assembly.
          </p>

          {/* Model toggle switcher */}
          <div className="grid grid-cols-2 gap-4 mb-6">
            <button 
              onClick={() => setActiveModel('transformer_seq')}
              className={`p-4 border rounded-sm font-mono text-xs font-bold transition-all text-left ${
                activeModel === 'transformer_seq'
                  ? 'border-signal-green bg-signal-green/5 text-signal-green glow-primary'
                  : 'border-white/10 text-[#D4D4D4] hover:border-white/20'
              }`}
            >
              <div className="uppercase">Transformer v4_Rec</div>
              <div className="text-[9px] text-[#D4D4D4]/60 mt-1 uppercase font-normal">Sequence classification model</div>
            </button>

            <button 
              onClick={() => setActiveModel('adcs_lstm')}
              className={`p-4 border rounded-sm font-mono text-xs font-bold transition-all text-left ${
                activeModel === 'adcs_lstm'
                  ? 'border-signal-green bg-signal-green/5 text-signal-green glow-primary'
                  : 'border-white/10 text-[#D4D4D4] hover:border-white/20'
              }`}
            >
              <div className="uppercase">ADCS LSTM CORE</div>
              <div className="text-[9px] text-[#D4D4D4]/60 mt-1 uppercase font-normal">Predictive timeseries stream</div>
            </button>
          </div>

          {/* Sensitivity Sliders */}
          <div className="space-y-4 mb-8">
            <div className="space-y-1">
              <div className="flex justify-between text-xs font-mono text-[#D4D4D4]">
                <span>ANOMALY THRESHOLD CONFIDENCE</span>
                <span className="text-signal-green font-bold">{anomalyThreshold}%</span>
              </div>
              <input 
                type="range" 
                min="50" 
                max="95" 
                value={anomalyThreshold}
                onChange={e => setAnomalyThreshold(Number(e.target.value))}
                className="w-full accent-signal-green cursor-pointer bg-[#0D0D0D] h-1.5 rounded-lg outline-none"
              />
              <div className="flex justify-between text-[9px] font-mono text-[#D4D4D4]/40">
                <span>SENSITIVE TO DRIFT (50%)</span>
                <span>CONSERVATIVE ALARM (95%)</span>
              </div>
            </div>
          </div>
        </div>

        {/* Recalibration triggers */}
        <div className="border-t border-white/10 pt-6 mt-6 flex flex-col sm:flex-row items-center justify-between gap-4 font-mono">
          <div>
            <div className="text-[10px] text-[#D4D4D4] uppercase font-bold tracking-wider">Classification Confidence</div>
            <div className="text-2xl font-black text-signal-green tracking-widest">{confidenceScore}%</div>
          </div>

          <button 
            onClick={handleRecalibrate}
            disabled={isCalibrating}
            className="px-6 py-3.5 bg-signal-green text-black font-bold hover:bg-[#D4FF00] hover:scale-[1.02] active:scale-[0.98] transition-all rounded-sm cursor-pointer border border-transparent glow-primary whitespace-nowrap uppercase tracking-widest text-xs"
          >
            {isCalibrating ? "RE-TUNING MODELS..." : "RECALIBRATE AI CORE"}
          </button>
        </div>
      </div>

      {/* Streams list panel */}
      <div className="w-full lg:w-96 bg-[#1A1A1A] border border-white/10 p-5 rounded-sm flex flex-col h-fit shadow-md font-mono">
        <h3 className="text-xs font-bold text-white uppercase tracking-wider border-b border-white/10 pb-3 mb-4">
          TELEMETRY ANOMALY DETECTIONS
        </h3>

        <div className="space-y-3">
          {telemetryStreams.map((stream, idx) => (
            <div key={idx} className="bg-[#000000]/40 border border-white/5 p-3.5 rounded-sm flex justify-between items-center">
              <div>
                <div className="text-xs font-bold text-white uppercase truncate max-w-[180px]">{stream.name}</div>
                <div className="text-[10px] text-data-blue mt-0.5">METRIC_VAL: {stream.value}</div>
              </div>

              <div className="flex items-center gap-2">
                <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded-sm ${
                  stream.status === 'NOMINAL' ? 'text-signal-green bg-signal-green/10' :
                  stream.status === 'WARNING' ? 'text-secondary bg-secondary/10' : 'text-[#FF3B30] bg-threat-red/10 animate-pulse'
                }`}>
                  {stream.status}
                </span>
                
                {stream.status === 'NOMINAL' ? (
                  <Check className="w-3.5 h-3.5 text-signal-green" />
                ) : (
                  <Warning className={`w-3.5 h-3.5 ${stream.status === 'WARNING' ? 'text-secondary' : 'text-[#FF3B30]'}`} />
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-6 p-3 bg-[#0D0D0D] border border-dashed border-white/10 rounded-sm text-[10px] text-[#D4D4D4]/80 leading-relaxed">
          <span className="text-signal-green font-bold">INFO:</span> Classification relies on multi-channel neural streams. If attitude warning is triggered, execute orbit recalibration immediately.
        </div>
      </div>

    </div>
  );
}
