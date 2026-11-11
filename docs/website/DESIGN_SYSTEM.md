# MDRAP Product Website Design System

This document outlines the design tokens, typography, component specifications, and aesthetic guidelines used across the MDRAP product web experience.

---

## 1. Aesthetic Identity & Purpose

MDRAP targets institutional quantitative developers, trading infrastructure leads, and risk controllers. The design avoids consumer SaaS clichés (e.g. pastel gradients, floating illustration blobs, vague buzzwords) in favor of:
- **Restrained Dark Theme**: Deep slate and navy tones matching institutional trading terminal environments.
- **Data-Dense Layout**: Clear tables, code blocks, and diagrams prioritizing clarity over decorative space.
- **Engineering Groundedness**: Every claim is tied to a reproducible benchmark, code snippet, or cryptographic theorem.

---

## 2. Color Palette

| Token | Hex Value | Role |
| :--- | :--- | :--- |
| `--bg-primary` | `#0b0f19` | Main page background (deep obsidian) |
| `--bg-secondary` | `#111827` | Card and container background |
| `--bg-tertiary` | `#1f2937` | Terminal bars, active table rows, borders |
| `--border-color` | `#374151` | Default structural border |
| `--border-light` | `#4b5563` | Hover states and emphasized borders |
| `--text-primary` | `#f9fafb` | Primary text and headlines |
| `--text-secondary`| `#9ca3af` | Secondary body text and subtitles |
| `--text-muted` | `#6b7280` | Labels, timestamps, subtle disclaimers |
| `--accent-blue` | `#3b82f6` | Primary action buttons and links |
| `--accent-green` | `#10b981` | Healthy indicators, valid statuses, verified badges |
| `--accent-amber` | `#f59e0b` | Suspicious anomalies, warnings, sequence gaps |
| `--accent-crimson` | `#ef4444` | Invalid errors, crossed spreads, corrupted frames |

---

## 3. Typography

- **System UI Font**: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif` for body and interface elements.
- **Monospace Font**: `ui-monospace, "SFMono-Regular", "SF Mono", Menlo, Consolas, "Liberation Mono", monospace` for code, terminal outputs, hashes, and data matrices.

---

## 4. Key Interactive Components

1. **Terminal Simulator (`website/js/main.js`)**: An in-browser simulator displaying the 15-step reliability verification sequence line-by-line with color-coded log lines.
2. **Data Tables (`website/benchmarks.html`)**: Styled financial-grade tables with monospaced latency percentiles (mean, p50, p95, p99, p99.9).
3. **Pilot Application Form (`website/contact.html`)**: Structured multi-field technical inquiry storing submissions in browser local storage and outputting a cryptographic confirmation hash.
