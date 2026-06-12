# 🛰️ DeadSat Resurrection

> **Autonomous Satellite Fault Detection, Classification, Recovery & Secure Command Uplink Platform**
>
> **FAR AWAY 2026 Hackathon**
>
> 🚀 Space & Aerospace • 🤖 Agentic Systems • 🔐 Cybersecurity

---

# Overview

DeadSat Resurrection is an autonomous cyber-forensic satellite recovery platform designed to detect, diagnose, and recover failed satellites with minimal human intervention.

Traditional satellite recovery workflows require mission operators to manually analyze telemetry, identify faults, prepare recovery procedures, wait for ground contact windows, and uplink commands. This process typically takes **48–96 hours**.

DeadSat Resurrection reduces this recovery timeline to **under 90 seconds** using AI-powered anomaly detection, fault classification, autonomous recovery planning, and secure command execution.

---

# Problem Statement

Satellites operating in orbit are vulnerable to numerous failure modes:

* Single Event Upsets (SEUs) caused by radiation
* Software crashes and reboot loops
* Firmware corruption
* Unauthorized command injection
* Ground communication disruptions

When a spacecraft enters an anomalous state, recovery is often slow, expensive, and heavily dependent on human operators.

Mission downtime can result in:

* Loss of scientific data
* Communication outages
* Reduced mission lifespan
* Permanent spacecraft failure

---

# Solution

DeadSat Resurrection provides a fully autonomous recovery pipeline:

```text
Live Telemetry
      │
      ▼
Anomaly Detection
      │
      ▼
Fault Classification
      │
      ▼
Recovery Planning
      │
      ▼
Secure Command Generation
      │
      ▼
Ground Contact Scheduling
      │
      ▼
Autonomous Recovery
```

Target recovery time:

```text
< 90 Seconds
```

---

# System Architecture

```text
┌─────────────────────────────────────────────────────┐
│                  Raspberry Pi 4 #1                  │
│                                                     │
│  ┌──────────┐    ┌──────────┐    ┌───────────────┐  │
│  │ Satellite│───▶│ FastAPI  │───▶│  LangGraph   │  │
│  │ Emulator │    │  :8000   │    │ Recovery Agent│  │
│  │ (AI-2)   │◀───│          │◀───│    (AI-2)    |  │
│  └──────────┘    └──────────┘    └───────────────┘  │
│       │               │                  │          │
│       │          ┌────▼────┐    ┌────────▼──────┐   │
│       │          │Isolation│    │ Dilithium PQC │   │
│       │          │ Forest +│    │ Command Sign  │   │
│       │          │Transformer│  │ Service (CY-1)│   │
│       │          │  (AI-1) │    │     :8001     │   │
│       │          └─────────┘    └───────────────┘   │
│       │                                             │
│  ┌────▼──────────────────────────────────────────┐  │
│  │          React Dashboard (FE-1) :3000         │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────┐
│                  Raspberry Pi 4 #2                  │
│                                                     │
│      RTL-SDR + NOAA 137 MHz RF Monitoring           │
└─────────────────────────────────────────────────────┘
```

---

# Key Features

## 🤖 Autonomous Recovery

* Automatic fault diagnosis
* Recovery procedure selection
* Autonomous command generation
* Recovery verification

## 🛰️ Satellite Digital Twin

Simulates:

* OBC (On-Board Computer)
* ADCS (Attitude Determination & Control)
* Power System
* Communications System

Supports:

* Fault injection
* Telemetry streaming
* Recovery validation

## 🧠 AI-Powered Fault Intelligence

* Isolation Forest anomaly detection
* Transformer Encoder classification
* Confidence scoring
* Multi-fault support

## 🔐 Post-Quantum Security

* CRYSTALS-Dilithium command signing
* Command verification
* Tamper-evident audit trail
* Secure uplink workflow

## 🌍 Orbital Awareness

* TLE-based orbit analysis
* Ground contact prediction
* Satellite pass estimation
* Live orbital updates

---

# Repository Structure

```text
DEADSAT-RESURRECTION/

├── agents/
│   ├── recovery_agent.py
│   └── procedure_library.json
│
├── emulator/
│   ├── satellite_emulator.py
│   └── contact_calculator.py
│
├── models/
│   ├── satellite_fault_classifier.py
│   ├── satellite_fault_classifier_V2.py
│   └── satellite_fault_classifier_tle.py
│
├── data/
│   ├── input.csv
│   ├── input__1_.csv
│   ├── input__2_.csv
│   └── training_baselines.csv
│
├── docs/
│   ├── deadsat_postman_collection.json
│   ├── Satellite_Fault_Recovery_Design.docx
│   └── CHANGES_V1_TO_V2.md
│
├── main.py
├── real_data_fetcher.py
├── satellite_catalog.py
├── requirements.txt
├── README.md
└── .gitignore
```

---

# AI-1: Fault Detection & Classification

## Anomaly Detection

An Isolation Forest continuously monitors telemetry and orbital data to identify abnormal spacecraft behavior.

Detects:

* Sudden state changes
* Orbit deviations
* Communication anomalies
* Unexpected subsystem behavior

---

## Transformer Fault Classifier

The Transformer Encoder predicts:

```text
SEU
SOFTWARE_BUG
FIRMWARE_CORRUPTION
COMMAND_INJECTION
```

Output:

```text
Fault Type
Confidence Score
Anomaly Flag
```

---

# TLE-Based Orbital Fault Classifier (Version 2)

## Why Version 2?

The original classifier was designed for telemetry streams.

The available datasets consisted of orbital element data from CelesTrak/NORAD sources, requiring the classifier to be redesigned around orbital mechanics rather than onboard telemetry.

Input parameters include:

```text
OBJECT_NAME
OBJECT_ID
EPOCH
MEAN_MOTION
ECCENTRICITY
INCLINATION
RA_OF_ASC_NODE
ARG_OF_PERICENTER
MEAN_ANOMALY
NORAD_CAT_ID
BSTAR
REV_AT_EPOCH
MEAN_MOTION_DOT
MEAN_MOTION_DDOT
```

---

## Derived Features

```text
ECC_DELTA
REV_DELTA
TLE_AGE_HOURS
BSTAR_ANOMALY
MEAN_MOTION_ANOMALY
```

---

## Classification Logic

| Fault               | Detection Logic                   |
| ------------------- | --------------------------------- |
| SEU                 | ECC_DELTA > 0.01                  |
| SOFTWARE_BUG        | REV_DELTA ≤ 0                     |
| FIRMWARE_CORRUPTION | Abnormal BSTAR or MEAN_MOTION_DOT |
| COMMAND_INJECTION   | TLE age > 72h                     |
| NORMAL              | No anomaly                        |

---

# AI-2: Autonomous Recovery Engine

The recovery engine is built using **LangGraph** and executes a structured recovery workflow.

```text
START
  │
  ▼
Load Procedures
  │
  ▼
Select Recovery Procedure
  │
  ▼
Generate Commands
  │
  ▼
Request Command Signing
  │
  ▼
Schedule Uplink
  │
  ▼
Execute Recovery
  │
  ▼
Monitor Recovery
  │
  ▼
SUCCESS / FAILURE
```

Recovery procedures are stored in:

```text
agents/procedure_library.json
```

and can be expanded without changing recovery-agent logic.

---

# Supported Fault Types

| Fault Type          | Description                        |
| ------------------- | ---------------------------------- |
| SEU                 | Radiation-induced bit flip         |
| SOFTWARE_BUG        | OBC crash loop or software failure |
| FIRMWARE_CORRUPTION | Corrupted firmware image           |
| COMMAND_INJECTION   | Unauthorized command execution     |

Each recovery procedure includes primary and fallback strategies.

---

# Data Sources

## Training Data

Combined datasets:

```text
input.csv
input__1_.csv
input__2_.csv
```

Dataset size:

```text
849+ orbital records
```

Augmented to approximately:

```text
1860+ training sequences
```

---

## Live Data Sources

### SatNOGS

Used for:

* Telemetry ingestion
* Historical observations
* Ground station integration

### N2YO

Used for:

* Live TLE retrieval
* Orbit updates
* Ground-pass calculations

Example NORAD targets:

* ISS (25544)
* NOAA-19 (33591)
* AO-10 (14129)
* AMSAT OSCAR-7 (7530)
* CUTE-1 / CO-55 (27844)

---

# API Endpoints

| Method | Endpoint             | Purpose                    |
| ------ | -------------------- | -------------------------- |
| GET    | `/health`            | System health              |
| GET    | `/telemetry`         | Latest telemetry           |
| GET    | `/telemetry/history` | Historical telemetry       |
| GET    | `/contact`           | Ground contact prediction  |
| POST   | `/fault/inject`      | Inject demonstration fault |
| POST   | `/recovery/trigger`  | Start recovery workflow    |
| POST   | `/reset`             | Reset satellite state      |

---

# Quick Start

## Clone Repository

```bash
git clone https://github.com/DevashyaManojbhaiJethva/DEADSAT-RESURRECTION.git
cd DEADSAT-RESURRECTION
```

## Install Dependencies

```bash
pip install -r requirements.txt
```

## Run

```bash
python main.py
```

---

## Swagger Documentation

```text
http://localhost:8000/docs
```

---

# Demo Flow

```text
1. Start FastAPI Server
2. Open Dashboard
3. Verify Healthy Satellite
4. Inject SEU Fault
5. Detect Anomaly
6. Classify Fault
7. Generate Recovery Plan
8. Schedule Contact Window
9. Execute Recovery Commands
10. Return Satellite to Nominal State
```

Expected recovery time:

```text
< 90 Seconds
```

---

# Hardware

## Raspberry Pi 4 #1

Runs:

* FastAPI Server
* Satellite Emulator
* Transformer Classifier
* Isolation Forest
* LangGraph Recovery Agent
* Command Signing Service

## Raspberry Pi 4 #2

Runs:

* RTL-SDR Receiver
* NOAA 137 MHz Monitoring
* RF Spectrum Visualization

---

# Technology Stack

### Backend

* Python
* FastAPI
* LangGraph
* Uvicorn

### Machine Learning

* PyTorch
* Scikit-Learn
* Isolation Forest
* Transformer Encoder

### Space Technologies

* SatNOGS
* N2YO
* TLE Analysis
* Orbit Prediction

### Security

* CRYSTALS-Dilithium
* Post-Quantum Cryptography
* Secure Command Signing

---

# Team

| Member | Responsibility                                                 |
| ------ | -------------------------------------------------------------- |
| AI-1   | Anomaly Detection & Transformer Fault Classification           |
| AI-2   | Satellite Emulator, FastAPI Backend & LangGraph Recovery Agent |
| FE-1   | React Dashboard Development                                    |
| FE-2   | Frontend Integration & API Connectivity                        |
| CY-1   | Post-Quantum Security & Command Signing                        |

---

# Innovation Highlights

✅ Autonomous Satellite Recovery

✅ Agentic AI Recovery Workflows

✅ Satellite Digital Twin Emulator

✅ Transformer-Based Fault Classification

✅ TLE-Based Orbital Intelligence

✅ Ground Contact Prediction

✅ Post-Quantum Secure Uplink

✅ Real-Time Telemetry Monitoring

✅ Space + AI + Cybersecurity Integration

---

# Future Work

* Real CubeSat deployment
* CCSDS packet support
* SDR telemetry decoding
* Multi-satellite constellation support
* Reinforcement-learning recovery optimization
* Autonomous mission planning

---

# FAR AWAY 2026

### Recovering Satellites in Seconds, Not Days.

**Space × AI × Cybersecurity** 🚀🛰️🔐
