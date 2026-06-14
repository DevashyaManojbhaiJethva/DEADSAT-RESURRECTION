/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

export type ScreenType = 'mission' | 'telemetry' | 'diagnostics' | 'security' | 'operator' | 'satellite-dashboard' | 'operator-control';

export interface TelemetryState {
  powerArray: number;
  adcsPitch: number;
  adcsYaw: number;
  adcsStability: 'NOMINAL' | 'WARN' | 'CRITICAL';
  commsBandwidth: number; // Gbps
  obcCpu: number;
  obcMem: number;
  altitude: number;
  velocity: number; // km/s
  lat: number;
  lng: number;
  temperature: number; // Kelvins
}

export interface SystemLog {
  id: string;
  timestamp: string;
  message: string;
  type: 'nominal' | 'warning' | 'critical' | 'info';
  category: string;
}

export interface CopilotMessage {
  id: string;
  timestamp: string;
  text: string;
  type: 'info' | 'warning' | 'alert' | 'success';
}

export interface OperatorCommand {
  id: string;
  command: string;
  output: string;
  timestamp: string;
  status: 'success' | 'error' | 'pending';
}

export interface SatelliteState {
  name: string;
  noradId: string;
  orbitClass: string;
  decayTimeSeconds: number; // countdown
  signalLock: boolean;
  activeKeyType: 'DILITHIUM' | 'RSA_VULNERABLE' | 'NONE';
  anomalyDetectionEnabled: boolean;
  automatedRecoveryActive: boolean;
}
