# landonkea-soliloquy — Design & Workflow

## High-Level Overview

```mermaid
graph TB
    subgraph "Entry Points"
        A[Web UI] --> B[FastAPI Routes]
        C[MQTT Bridge] --> B
        D[Scheduled Job] --> B
    end

    subgraph "Core"
        B --> E[actions.py]
        E --> F[Entry model]
        E --> G[EntryStore]
        E --> H[ObjectStore]
        E --> I[Transcriber]
        E --> J[Analyzer]
    end

    subgraph "Storage"
        G --> K[(PostgreSQL)]
        H --> L[(MinIO/S3)]
    end

    subgraph "Analysis"
        J --> M[Claude/OpenRouter/Gemini]
        D --> J
    end
```

## Entry Creation Flow

```mermaid
sequenceDiagram
    participant U as User
    participant W as Web UI
    participant A as actions.py
    participant T as Transcriber
    participant S as Storage
    participant DB as PostgreSQL

    alt Typed entry
        U->>W: Type text
        W->>A: add_entry(text)
    else Audio entry
        U->>W: Upload audio
        W->>T: Transcribe
        T-->>A: Transcript
        A->>S: Store audio
    else Video entry
        U->>W: Upload video
        W->>T: Extract audio + transcribe
        T-->>A: Transcript
        A->>S: Store video + audio
    end
    A->>DB: INSERT entry
    DB-->>A: Entry saved
    A-->>W: Success
```

## Analysis Flow

```mermaid
flowchart TD
    A[Scheduled job runs] --> B[Query entries in date range]
    B --> C[Filter by sharing flags]
    C --> D[Send to analyzer]
    D --> E{Provider?}
    E -->|Free| F[OpenRouter/Gemini]
    E -->|Claude| G[Anthropic API]
    F --> H[Get summary + topics]
    G --> H
    H --> I[Save to analysis_store]
    I --> J[Display on Analysis page]
```

## File Relationships

| File | Purpose | Used By |
|------|---------|---------|
| `src/soliloquy/entry.py` | Entry data model | Everything |
| `src/soliloquy/actions.py` | Core operations | Web, MQTT, Scheduler |
| `src/soliloquy/storage.py` | PostgreSQL store | Actions |
| `src/soliloquy/object_storage.py` | S3/MinIO store | Actions |
| `src/soliloquy/transcriber.py` | Whisper transcription | Actions |
| `src/soliloquy/analyzer.py` | AI analysis | Actions |
| `src/soliloquy/web/` | FastAPI app | User |
| `mqtt_bridge.py` | MQTT listener | makeItSoNumberOne |
| `scheduler.py` | Background analysis | Cron |
| `docker-compose.yml` | Postgres + MinIO + Mosquitto | Docker |

## draw.io

[Open in draw.io](https://app.diagrams.net/#RSoliloquy%20architecture)
