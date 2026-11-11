/**
 * MDRAP Institutional Website Interactions
 * Includes live simulated terminal runner, tabbed switchers, and pilot inquiry handling.
 */

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initDemoSimulator();
  initContactForm();
});

/* Tabs Switcher */
function initTabs() {
  const tabContainers = document.querySelectorAll("[data-tabs]");
  tabContainers.forEach(container => {
    const buttons = container.querySelectorAll("[data-tab-target]");
    const panels = container.querySelectorAll("[data-tab-content]");

    buttons.forEach(btn => {
      btn.addEventListener("click", () => {
        const target = btn.getAttribute("data-tab-target");

        buttons.forEach(b => {
          b.classList.remove("active", "btn-primary");
          b.classList.add("btn-outline");
        });
        panels.forEach(p => p.style.display = "none");

        btn.classList.add("active", "btn-primary");
        btn.classList.remove("btn-outline");

        const activePanel = container.querySelector(`[data-tab-content="${target}"]`);
        if (activePanel) {
          activePanel.style.display = "block";
        }
      });
    });
  });
}

/* Demo Simulator */
function initDemoSimulator() {
  const runBtn = document.getElementById("run-sim-btn");
  const outputEl = document.getElementById("sim-terminal-output");
  const resetBtn = document.getElementById("reset-sim-btn");

  if (!runBtn || !outputEl) return;

  const simSteps = [
    "[INIT] Initializing passive observer sidecar (v6.0.0)...",
    "[STATUS] Connected to SYNTHETIC_BINANCE and SYNTHETIC_COINBASE feeds.",
    "[INGEST] Seq:1001 BINANCE BTC-USD Bid:65200.0 Ask:65200.5 -> Quality: VALID",
    "[INGEST] Seq:501  COINBASE BTC-USD Bid:65200.1 Ask:65200.6 -> Quality: VALID",
    "[PROVENANCE] Wire frame bound to payload SHA-256: fedd0ea63dcb92ffd...",
    "[QUALITY] 15 deterministic microstructure rules active. Zero violations.",
    "[ANOMALY INJECTION] Target feed BINANCE sequence jumped 1003 -> 1250 (246 ticks dropped)!",
    "[QUARANTINE] Event evt-demo-1250 quarantined as SUSPICIOUS (Reason: SEQUENCE_GAP).",
    "[INCIDENT] Opened DataQualityIncident inc-1700000001-0001 [Severity: HIGH].",
    "[MERKLE] Anchored 7 events into RFC 6962 tree. Root: ee284cbf20b1a1f4...",
    "[TEARDOWN] Simulating process termination and host failure...",
    "[RECOVERY] Cold restart verified: Incident state recovered from SQLite WAL.",
    "[REPLAY] Deterministic corridor replay: 4 events replayed. Digest: 0d6931688745...",
    "[VERIFY] Standalone verifier (tools/verify_evidence.py): 100% AUTHENTIC.",
    "[COMPLETE] Forensic incident post-mortem report exported to disk."
  ];

  let isRunning = false;

  runBtn.addEventListener("click", () => {
    if (isRunning) return;
    isRunning = true;
    runBtn.disabled = true;
    runBtn.innerText = "Running Simulation...";
    outputEl.innerHTML = "";

    let i = 0;
    function printNext() {
      if (i < simSteps.length) {
        const line = document.createElement("div");
        line.style.marginBottom = "4px";
        line.style.fontFamily = "var(--font-mono)";
        line.style.fontSize = "12px";

        if (simSteps[i].includes("ANOMALY") || simSteps[i].includes("QUARANTINE")) {
          line.style.color = "var(--accent-amber)";
        } else if (simSteps[i].includes("COMPLETE") || simSteps[i].includes("VERIFY")) {
          line.style.color = "var(--accent-green)";
        } else if (simSteps[i].includes("TEARDOWN")) {
          line.style.color = "var(--accent-crimson)";
        } else {
          line.style.color = "#d1d5db";
        }

        line.innerText = simSteps[i];
        outputEl.appendChild(line);
        outputEl.scrollTop = outputEl.scrollHeight;
        i++;
        setTimeout(printNext, 350);
      } else {
        isRunning = false;
        runBtn.disabled = false;
        runBtn.innerText = "Re-Run Demonstration";
      }
    }
    printNext();
  });

  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      outputEl.innerHTML = '<span style="color:var(--text-muted)">Click "Run Local Demonstration" to simulate the 15-step reliability engine...</span>';
    });
  }
}

/* Contact Form Handler */
function initContactForm() {
  const form = document.getElementById("pilot-inquiry-form");
  const responseBox = document.getElementById("form-response-msg");

  if (!form) return;

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const formData = new FormData(form);
    const payload = {
      timestamp: new Date().toISOString(),
      institution: formData.get("institution") || "",
      contact_name: formData.get("contact_name") || "",
      email: formData.get("email") || "",
      role: formData.get("role") || "",
      target_venues: formData.getAll("venues") || [],
      estimated_volume: formData.get("volume") || "",
      primary_pain_point: formData.get("pain_point") || "",
    };

    // Store in browser local storage
    const existing = JSON.parse(localStorage.getItem("mdrap_pilot_inquiries") || "[]");
    existing.push(payload);
    localStorage.setItem("mdrap_pilot_inquiries", JSON.stringify(existing));

    form.reset();
    if (responseBox) {
      responseBox.style.display = "block";
      responseBox.innerHTML = `
        <div style="background-color: rgba(16, 185, 129, 0.1); border: 1px solid var(--accent-green); padding: 16px; border-radius: 6px; margin-top: 16px;">
          <h4 style="color: var(--accent-green); margin-bottom: 8px;">Inquiry Registered Successfully</h4>
          <p style="font-size: 13px; color: var(--text-secondary);">
            Thank you, <strong>${payload.contact_name}</strong>. Your technical pilot evaluation request for <strong>${payload.institution}</strong> has been logged to the local staging registry.
          </p>
          <p style="font-size: 12px; color: var(--text-muted); margin-top: 8px; font-family: var(--font-mono);">
            Record Hash: ${btoa(payload.timestamp + payload.email).substring(0, 24)}...
          </p>
        </div>
      `;
    }
  });
}
