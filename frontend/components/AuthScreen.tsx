import React, { useState } from 'react';
import { Key, Lock, Shield, Terminal } from './Icons';
import { login } from '../api';

interface AuthScreenProps {
  onAuthSuccess: (operatorId: string) => void;
  onCancel: () => void;
}

/** Real backend login screen; it never manufactures credentials or tokens. */
export default function AuthScreen({ onAuthSuccess, onCancel }: AuthScreenProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const handleAuthenticate = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await login(username, password);
      onAuthSuccess(username);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Authentication failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0D0D0D] flex items-center justify-center p-6 bg-grid font-sans relative">
      <div className="max-w-md w-full bg-[#1A1A1A]/95 border border-signal-green/20 p-8 rounded-sm shadow-[0_0_40px_rgba(0,0,0,0.85)]">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-full border border-signal-green/20 mb-4 bg-[#0D0D0D]"><Lock className="w-6 h-6 text-signal-green" /></div>
          <h1 className="font-display text-3xl font-black text-white tracking-tighter uppercase leading-none">Authentication</h1>
          <p className="font-mono text-[10px] text-[#D4D4D4]/60 uppercase tracking-widest mt-1">Verified operator session</p>
        </div>
        <form onSubmit={handleAuthenticate} className="space-y-6">
          <div className="space-y-4">
            <label className="block relative"><span className="font-mono text-[10px] uppercase font-bold text-[#D4D4D4]/60">Username</span><Terminal className="w-4 h-4 text-[#D4D4D4]/60 absolute left-3.5 bottom-3.5" /><input type="text" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required className="mt-1 w-full bg-[#0D0D0D]/60 border border-white/10 text-signal-green font-mono font-bold p-3.5 pl-10 rounded-sm focus:outline-none focus:border-signal-green" /></label>
            <label className="block relative"><span className="font-mono text-[10px] uppercase font-bold text-[#D4D4D4]/60">Password</span><Key className="w-4 h-4 text-[#D4D4D4]/60 absolute left-3.5 bottom-3.5" /><input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required className="mt-1 w-full bg-[#0D0D0D]/60 border border-white/10 text-signal-green font-mono p-3.5 pl-10 rounded-sm focus:outline-none focus:border-signal-green" /></label>
          </div>
          {error && <p role="alert" className="text-threat-red text-xs font-mono">{error}</p>}
          <button disabled={submitting} type="submit" className="w-full bg-signal-green disabled:opacity-60 text-black p-4 font-display text-xs font-black uppercase tracking-widest flex items-center justify-center gap-2 rounded-sm"><Shield className="w-4 h-4" /><span>{submitting ? 'VERIFYING…' : 'AUTHENTICATE'}</span></button>
          <button type="button" onClick={onCancel} className="w-full text-center text-[10px] text-[#D4D4D4]/60 hover:text-white py-1 uppercase font-bold tracking-widest">Cancel security engagement</button>
        </form>
      </div>
    </div>
  );
}
